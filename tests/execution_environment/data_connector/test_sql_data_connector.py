import pytest
import os
import yaml
import json
import random
import datetime
import sqlite3

import pandas as pd

from great_expectations.execution_environment.data_connector import (
    SqlDataConnector,
)
from great_expectations.core.batch import (
    BatchRequest,
    BatchDefinition,
    PartitionDefinition,
)
from great_expectations.data_context.util import (
    instantiate_class_from_config
)
from tests.test_utils import (
    create_fake_data_frame,
    create_files_in_directory,
)
from great_expectations.execution_engine.sqlalchemy_execution_engine import (
    SqlAlchemyExecutionEngine
)
from great_expectations.data_context.util import file_relative_path

from great_expectations.data_context.util import instantiate_class_from_config

@pytest.fixture
def test_cases_for_sql_data_connector_sqlite_execution_engine():
    # TODO: Switch this to an actual ExecutionEngine

    db_file = file_relative_path(
        __file__, os.path.join("..", "..", "test_sets", "test_cases_for_sql_data_connector.db")
    )
    db = sqlite3.connect(db_file)
    return db


def test_basic_self_check(test_cases_for_sql_data_connector_sqlite_execution_engine):
    # base_directory = str(tmp_path_factory.mktemp("test_basic_self_check"))
    db = test_cases_for_sql_data_connector_sqlite_execution_engine

    config = yaml.load("""
    name: my_sql_data_connector
    execution_environment_name: FAKE_EE_NAME

    assets:
        table_partitioned_by_date_column__A:
            #table_name: events # If table_name is omitted, then the table_name defaults to the asset name
            splitter:
                class_name: ColumnValueSplitter
                column_name: date
    """, yaml.FullLoader)
    config["execution_engine"] = db

    my_data_connector = SqlDataConnector(**config)

    report = my_data_connector.self_check()
    print(json.dumps(report, indent=2))

    assert report == {
        "class_name": "SqlDataConnector",
        "data_asset_count": 1,
        "example_data_asset_names": [
            "table_partitioned_by_date_column__A"
        ],
        "data_assets": {
            "table_partitioned_by_date_column__A": {
                "batch_definition_count": 30,
                "example_data_references": [
                    "2020-01-01",
                    "2020-01-02",
                    "2020-01-03"
                ]
            }
        },
        "unmatched_data_reference_count": 0,
        "example_unmatched_data_references": []
    }


def test_example_A(test_cases_for_sql_data_connector_sqlite_execution_engine):
    db = test_cases_for_sql_data_connector_sqlite_execution_engine

    config = yaml.load("""
    name: my_sql_data_connector
    execution_environment_name: FAKE_EE_NAME

    assets:
        table_partitioned_by_date_column__A:
            splitter:
                class_name: ColumnValueSplitter
                column_name: date

    """, yaml.FullLoader)
    config["execution_engine"] = db

    my_data_connector = SqlDataConnector(**config)

    report = my_data_connector.self_check()
    print(json.dumps(report, indent=2))

    # TODO: Flesh this out once the implementation actually works to this point
    # assert report == {
    #     "class_name": "SqlDataConnector",
    #     "data_asset_count": 1,
    #     "example_data_asset_names": [
    #         "table_partitioned_by_date_column__A"
    #     ],
    #     "data_assets": {
    #         "table_partitioned_by_date_column__A": {
    #             "batch_definition_count": 30,
    #             "example_data_references": [
    #                 "2020-01-01",
    #                 "2020-01-02",
    #                 "2020-01-03"
    #             ]
    #         }
    #     },
    #     "unmatched_data_reference_count": 0,
    #     "example_unmatched_data_references": []
    # }


def test_example_B(test_cases_for_sql_data_connector_sqlite_execution_engine):
    db = test_cases_for_sql_data_connector_sqlite_execution_engine

    config = yaml.load("""
    name: my_sql_data_connector
    execution_environment_name: FAKE_EE_NAME

    assets:
        table_partitioned_by_timestamp_column__B:
            splitter:
                class_name: ColumnValueSplitter
                column_name: timestamp
                transformation_method: convert_datetime_to_date
                transformation_method_kwargs:
                    ???
    """, yaml.FullLoader)
    config["execution_engine"] = db

    my_data_connector = SqlDataConnector(**config)

    report = my_data_connector.self_check()
    print(json.dumps(report, indent=2))

    # TODO: Flesh this out once the implementation actually works to this point
    # assert report == {
    #     "class_name": "SqlDataConnector",
    #     "data_asset_count": 1,
    #     "example_data_asset_names": [
    #         "table_partitioned_by_date_column__A"
    #     ],
    #     "data_assets": {
    #         "table_partitioned_by_date_column__A": {
    #             "batch_definition_count": 30,
    #             "example_data_references": [
    #                 "2020-01-01",
    #                 "2020-01-02",
    #                 "2020-01-03"
    #             ]
    #         }
    #     },
    #     "unmatched_data_reference_count": 0,
    #     "example_unmatched_data_references": []
    # }

def test_example_C(test_cases_for_sql_data_connector_sqlite_execution_engine):
    db = test_cases_for_sql_data_connector_sqlite_execution_engine

    config = yaml.load("""
    name: my_sql_data_connector
    execution_environment_name: FAKE_EE_NAME

    assets:
        table_partitioned_by_regularly_spaced_incrementing_id_column__C:
            splitter:
                class_name: ColumnValueSplitter
                column_name: index
                transformation_method: ???
                transformation_method_kwargs:
                    ???
    """, yaml.FullLoader)
    config["execution_engine"] = db

    my_data_connector = SqlDataConnector(**config)

    report = my_data_connector.self_check()
    print(json.dumps(report, indent=2))

    # TODO: Flesh this out once the implementation actually works to this point
    # assert report == {
    #     "class_name": "SqlDataConnector",
    #     "data_asset_count": 1,
    #     "example_data_asset_names": [
    #         "table_partitioned_by_date_column__A"
    #     ],
    #     "data_assets": {
    #         "table_partitioned_by_date_column__A": {
    #             "batch_definition_count": 30,
    #             "example_data_references": [
    #                 "2020-01-01",
    #                 "2020-01-02",
    #                 "2020-01-03"
    #             ]
    #         }
    #     },
    #     "unmatched_data_reference_count": 0,
    #     "example_unmatched_data_references": []
    # }



# ['table_partitioned_by_date_column__A',
#  'table_partitioned_by_timestamp_column__B',
#  'table_partitioned_by_regularly_spaced_incrementing_id_column__C',
#  'table_partitioned_by_irregularly_spaced_incrementing_id_with_spacing_in_a_second_table__D',
#  'table_containing_id_spacers_for_D',
#  'table_partitioned_by_incrementing_batch_id__E',
#  'table_partitioned_by_foreign_key__F',
#  'table_with_fk_reference_from_F',
#  'table_partitioned_by_multiple_columns__G',
#  'table_that_should_be_partitioned_by_random_hash__H']

# def test_example_A(test_cases_for_sql_data_connector_sqlite_execution_engine):
#     db = test_cases_for_sql_data_connector_sqlite_execution_engine

#     config = yaml.load("""
#     name: my_sql_data_connector
#     execution_environment_name: FAKE_EE_NAME

#     assets:
#         table_partitioned_by_date_column__A:
#             #table_name: events # If table_name is omitted, then the table_name defaults to the asset name
#             splitter:
#                 column_name: date

#         table_partitioned_by_timestamp_column__B:
#             splitter:
#                 column_name: timestamp

#         table_partitioned_by_regularly_spaced_incrementing_id_column__C:
#             splitter:
#                 column_name: date

#         table_partitioned_by_irregularly_spaced_incrementing_id_with_spacing_in_a_second_table__D:
#             splitter:
#                 column_name: date

#         table_containing_id_spacers_for_D:
#             splitter:
#                 column_name: date

#         table_partitioned_by_incrementing_batch_id__E:
#             splitter:
#                 column_name: date

#         table_partitioned_by_foreign_key__F:
#             splitter:
#                 column_name: date

#         table_with_fk_reference_from_F:
#             splitter:
#                 column_name: date

#         table_partitioned_by_multiple_columns__G:
#             splitter:
#                 column_name: date

#         table_that_should_be_partitioned_by_random_hash__H':
#             splitter:
#                 column_name: date

#     """, yaml.FullLoader)
#     config["execution_engine"] = db

#     my_data_connector = SqlDataConnector(**config)

#     report = my_data_connector.self_check()
#     print(json.dumps(report, indent=2))

#     assert report == {
#         "class_name": "SqlDataConnector",
#         "data_asset_count": 1,
#         "example_data_asset_names": [
#             "table_partitioned_by_date_column__A"
#         ],
#         "data_assets": {
#             "table_partitioned_by_date_column__A": {
#                 "batch_definition_count": 30,
#                 "example_data_references": [
#                     "2020-01-01",
#                     "2020-01-02",
#                     "2020-01-03"
#                 ]
#             }
#         },
#         "unmatched_data_reference_count": 0,
#         "example_unmatched_data_references": []
#     }