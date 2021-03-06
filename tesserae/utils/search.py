"""Helper class and functions for running search

AsynchronousSearcher provides normal Tesserae search capabilities.

bigram_search enables lookup of bigrams for specified units of specified texts
"""
import multiprocessing
import queue
import time
import traceback

from tesserae.db import TessMongoConnection
from tesserae.db.entities import Search, Unit
import tesserae.matchers


class AsynchronousSearcher:
    """Asynchronous Tesserae search resource holder

    Attributes
    ----------
    workers : list of SearchProcess
        the workers this object has created
    queue : multiprocessing.Queue
        work queue which workers listen on

    """

    def __init__(self, num_workers, db_cred):
        """Store parameters to be used in intializing resources

        Parameters
        ----------
        num_workers : int
            number of workers to create
        db_cred : dict
            credentials to access the database; arguments should be given for
            TessMongoConnection.__init__ in kwarg unpacking format

        """
        self.num_workers = num_workers
        self.db_cred = db_cred

        self.queue = multiprocessing.Queue()
        self.workers = []
        for _ in range(self.num_workers):
            cur_proc = SearchProcess(self.db_cred, self.queue)
            cur_proc.start()
            self.workers.append(cur_proc)

    def cleanup(self, *args):
        """Clean up system resources being used by this object

        This method should be called by exit handlers in the main script

        """
        try:
            while True:
                self.queue.get_nowait()
        except queue.Empty:
            pass
        for _ in range(len(self.workers)):
            self.queue.put((None, None, None))
        for worker in self.workers:
            worker.join()

    def queue_search(self, results_id, search_type, search_params):
        """Queues search for processing

        Parameters
        ----------
        results_id : str
            UUID for identifying the search request
        search_type : str
            identifier for type of search to perform.  Available options are
            defined in tesserae.matchers.search_types (located in the
            __init__.py file).
        search_params : dict
            search parameters

        """
        self.queue.put_nowait((results_id, search_type, search_params))


class SearchProcess(multiprocessing.Process):
    """Worker process waiting for search to execute

    Listens on queue for work to do
    """

    def __init__(self, db_cred, queue):
        """Constructs a search worker

        Parameters
        ----------
        db_cred : dict
            credentials to access the database; arguments should be given for
            TessMongoConnection.__init__ in keyword format
        queue : multiprocessing.Queue
            mechanism for receiving search requests

        """
        super().__init__(target=self.await_job, args=(db_cred, queue))

    def await_job(self, db_cred, queue):
        """Waits for search job"""
        connection = TessMongoConnection(**db_cred)
        while True:
            results_id, search_type, search_params = queue.get(block=True)
            if results_id is None:
                break
            self.run_search(connection, results_id, search_type, search_params)

    def run_search(self, connection, results_id, search_type, search_params):
        """Executes search"""
        start_time = time.time()
        parameters = {
            'source': {
                'object_id': str(search_params['source'].text.id),
                'units': search_params['source'].unit_type
            },
            'target': {
                'object_id': str(search_params['target'].text.id),
                'units': search_params['target'].unit_type
            },
            'method': {
                'name': search_type,
                'feature': search_params['feature'],
                'stopwords': search_params['stopwords'],
                'freq_basis': search_params['frequency_basis'],
                'max_distance': search_params['max_distance'],
                'distance_basis': search_params['distance_metric']
            }
        }
        results_status = Search(
            results_id=results_id,
            status=Search.INIT, msg='',
            parameters=parameters
        )
        connection.insert(results_status)
        try:
            search_id = results_status.id
            matcher = tesserae.matchers.matcher_map[search_type](connection)
            results_status.status = Search.RUN
            connection.update(results_status)
            matches = matcher.match(search_id, **search_params)
            connection.insert_nocheck(matches)

            results_status.status = Search.DONE
            results_status.msg = 'Done in {} seconds'.format(
                time.time() - start_time)
            connection.update(results_status)
        # we want to catch all errors and log them into the Search entity
        except:  # noqa: E722
            results_status.status = Search.FAILED
            results_status.msg = traceback.format_exc()
            connection.update(results_status)


def check_cache(connection, source, target, method):
    """Check whether search results are already in the database

    Parameters
    ----------
    connection : TessMongoConnection
    source
        See API documentation for form
    target
        See API documentation for form
    method
        See API documentation for form

    Returns
    -------
    UUID or None
        If the search results are already in the database, return the
        results_id associated with them; otherwise return None

    Notes
    -----
    Helpful links
        https://docs.mongodb.com/manual/tutorial/query-embedded-documents/
        https://docs.mongodb.com/manual/tutorial/query-arrays/
        https://docs.mongodb.com/manual/reference/operator/query/and/
    """
    found = [
        Search.json_decode(f)
        for f in connection.connection[Search.collection].find({
            'parameters.source.object_id': str(source['object_id']),
            'parameters.source.units': source['units'],
            'parameters.target.object_id': str(target['object_id']),
            'parameters.target.units': target['units'],
            'parameters.method.name': method['name'],
            'parameters.method.feature': method['feature'],
            '$and': [
                {'parameters.method.stopwords': {'$all': method['stopwords']}},
                {'parameters.method.stopwords': {
                    '$size': len(method['stopwords'])}}
            ],
            'parameters.method.freq_basis': method['freq_basis'],
            'parameters.method.max_distance': method['max_distance'],
            'parameters.method.distance_basis': method['distance_basis']
        })
    ]
    if found:
        status_found = connection.find(
            Search.collection,
            _id=found[0].id)
        if status_found and status_found[0].status != Search.FAILED:
            return status_found[0].results_id
    return None


def _words_in_different_positions(unit, feature, word1_index, word2_index):
    word1_positions = set()
    word2_positions = set()
    for i, tok in enumerate(unit.tokens):
        cur_features = tok['features'][feature]
        if word1_index in cur_features:
            word1_positions.add(i)
        if word2_index in cur_features:
            word2_positions.add(i)
    return word1_positions - word2_positions and \
        word2_positions - word1_positions


def bigram_search(
        connection, word1_index, word2_index, feature, unit_type, text_ids):
    """Retrieves all Units containing the specified words

    Parameters
    ----------
    connection : TessMongoConnection
    word1_index, word2_index : int
        Feature index of words to be contained in a Unit
    feature : {'lemmata', 'form'}
        Feature type of words to search for
    unit_type : {'line', 'phrase'}
        Type of Units to look for
    text_ids : list of ObjectId
        The IDs of Texts whose Units are to be searched

    Returns
    -------
    list of Unit
        All Units of the specified texts and ``unit_type`` containing
        both ``word1_index`` and ``word2_index``
    """
    unit_candidates = connection.aggregate(
        Unit.collection,
        [
            {'$match': {'$expr': {'$in': ['$text', text_ids]}}},
            {'$match': {'tokens.features.'+feature: word1_index}},
            {'$match': {'tokens.features.'+feature: word2_index}},
        ]
    )
    results = []
    for u in unit_candidates:
        if _words_in_different_positions(u, feature, word1_index, word2_index):
            results.append(u)
    return results
