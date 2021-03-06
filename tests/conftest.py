"""Utility operations for unit tests across multiple modules.
"""
from pathlib import Path
import pytest

import getpass
import json
import os

import pymongo

from tesserae.db import TessMongoConnection, Text
from tesserae.utils import ingest_text


def pytest_addoption(parser):
    parser.addoption('--db-host', action='store', default='127.0.0.1',
                     help='IP of the test database host')
    parser.addoption('--db-port', action='store', default=27017, type=int,
                     help='Port that the test database listens on')
    parser.addoption('--db-user', action='store',
                     help='User to log into the test database as')
    parser.addoption('--db-pwd', action='store_true',
                     help='Pass this flag to input database password on start')
    parser.addoption('--db-name', action='store', default='tess_test',
                     help='Name of the test database to use.')


def pytest_configure(config):
    if config.option.db_pwd:
        password = getpass.getpass(prompt='Test Database Password: ')
        setattr(config.option, 'db_passwd', password)


@pytest.fixture(scope='session')
def mini_latin_metadata(tessfiles_latin_path):
    return [
        {
            'title': 'miniaeneid',
            'author': 'minivergil',
            'language': 'latin',
            'year': -19,
            'path': str(tessfiles_latin_path.joinpath('mini.aen.tess'))
        },
        {
            'title': 'miniphar',
            'author': 'minilucan',
            'language': 'latin',
            'year': 65,
            'path': str(tessfiles_latin_path.joinpath('mini.phar.tess'))
        },
    ]


@pytest.fixture(scope='session')
def mini_greek_metadata(tessfiles_greek_path):
    return [
        {
            'title': 'miniiliad',
            'author': 'minihomer',
            'language': 'greek',
            'year': -1260,
            'path': str(tessfiles_greek_path.joinpath('mini.il.tess'))
        },
        {
            'title': 'minigorgis',
            'author': 'miniplato',
            'language': 'greek',
            'year': -283,
            'path': str(tessfiles_greek_path.joinpath('mini.gorg.tess'))
        },
    ]


@pytest.fixture(scope='session')
def mini_punctuation_metadata(tessfiles_latin_path):
    return [
        {
            'title': 'minicivdei',
            'author': 'miniaug',
            'language': 'latin',
            'year': 426,
            'path': str(tessfiles_latin_path.joinpath('mini.aug.tess'))
        },
        {
            'title': 'minidiv',
            'author': 'minicicero',
            'language': 'latin',
            'year': -44,
            'path': str(tessfiles_latin_path.joinpath('mini.cic.tess'))
        },
    ]


@pytest.fixture(scope='session')
def tessfiles_path():
    return Path(__file__).resolve().parent.joinpath('tessfiles')


@pytest.fixture(scope='session')
def tessfiles_greek_path(tessfiles_path):
    return tessfiles_path.joinpath('grc')


@pytest.fixture(scope='session')
def tessfiles_latin_path(tessfiles_path):
    return tessfiles_path.joinpath('la')


@pytest.fixture(scope='session')
def connection(request):
    conf = request.config
    client = pymongo.MongoClient(host=conf.getoption('db_host'),
                                 port=conf.getoption('db_port'),
                                 username=conf.getoption('db_user',
                                                         default=None),
                                 password=conf.getoption('db_passwd',
                                                         default=None))
    return client[conf.getoption('db_name')]


@pytest.fixture(scope='session')
def test_data(connection, tessfiles):
    # Load in test entries from tests/test_db_entries.json
    directory = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(directory, 'test_db_entries.json'), 'r') as f:
        test_entries = json.load(f)

    for text in test_entries['texts']:
        text['path'] = os.path.join(tessfiles, text['path'])

    # Insert all of the docs
    # for collection, docs in test_entries.items():
    #     if len(docs) > 0:
    #         connection[collection].insert_many(docs)

    yield test_entries

    # Clean up the test database for a clean slate next time
    for collection in test_entries:
        connection[collection].delete_many({})


@pytest.fixture(scope='session')
def tessfiles():
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), 'tessfiles'))


@pytest.fixture(scope='session')
def minipop(request, mini_greek_metadata, mini_latin_metadata):
    conn = TessMongoConnection('localhost', 27017, None, None, 'minitess')
    conn.create_indices()
    for metadata in mini_greek_metadata:
        text = Text.json_decode(metadata)
        ingest_text(conn, text)
    for metadata in mini_latin_metadata:
        text = Text.json_decode(metadata)
        ingest_text(conn, text)
    yield conn
    for coll_name in conn.connection.list_collection_names():
        conn.connection.drop_collection(coll_name)
