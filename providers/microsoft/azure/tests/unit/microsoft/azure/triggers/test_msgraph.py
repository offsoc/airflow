# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import json
import locale
from base64 import b64decode, b64encode
from datetime import datetime
from os.path import dirname
from unittest.mock import patch
from uuid import uuid4

import pendulum
from msgraph_core import APIVersion

from airflow.exceptions import AirflowException
from airflow.providers.microsoft.azure.hooks.msgraph import KiotaRequestAdapterHook
from airflow.providers.microsoft.azure.triggers.msgraph import (
    MSGraphTrigger,
    ResponseSerializer,
)
from airflow.triggers.base import TriggerEvent

from tests_common.test_utils.file_loading import load_file_from_resources, load_json_from_resources
from tests_common.test_utils.operators.run_deferrable import run_trigger
from unit.microsoft.azure.base import Base
from unit.microsoft.azure.test_utils import (
    get_airflow_connection,
    mock_json_response,
    mock_response,
)

try:
    import importlib.util

    if not importlib.util.find_spec("airflow.sdk.bases.hook"):
        raise ImportError

    BASEHOOK_PATCH_PATH = "airflow.sdk.bases.hook.BaseHook"
except ImportError:
    BASEHOOK_PATCH_PATH = "airflow.hooks.base.BaseHook"


class TestMSGraphTrigger(Base):
    def test_run_when_valid_response(self):
        users = load_json_from_resources(dirname(__file__), "..", "resources", "users.json")
        response = mock_json_response(200, users)

        with self.patch_hook_and_request_adapter(response):
            trigger = MSGraphTrigger("users/delta", conn_id="msgraph_api")
            actual = run_trigger(trigger)

            assert len(actual) == 1
            assert isinstance(actual[0], TriggerEvent)
            assert actual[0].payload["status"] == "success"
            assert actual[0].payload["type"] == "builtins.dict"
            assert actual[0].payload["response"] == json.dumps(users)

    def test_run_when_response_is_none(self):
        response = mock_json_response(200)

        with self.patch_hook_and_request_adapter(response):
            trigger = MSGraphTrigger("users/delta", conn_id="msgraph_api")
            actual = run_trigger(trigger)

            assert len(actual) == 1
            assert isinstance(actual[0], TriggerEvent)
            assert actual[0].payload["status"] == "success"
            assert actual[0].payload["type"] is None
            assert actual[0].payload["response"] is None

    def test_run_when_response_cannot_be_converted_to_json(self):
        with self.patch_hook_and_request_adapter(AirflowException()):
            trigger = MSGraphTrigger("users/delta", conn_id="msgraph_api")
            actual = next(iter(run_trigger(trigger)))

            assert isinstance(actual, TriggerEvent)
            assert actual.payload["status"] == "failure"
            assert actual.payload["message"] == ""

    def test_run_when_response_is_bytes(self):
        content = load_file_from_resources(
            dirname(__file__), "..", "resources", "dummy.pdf", mode="rb", encoding=None
        )
        base64_encoded_content = b64encode(content).decode(locale.getpreferredencoding())
        response = mock_response(200, content)

        with self.patch_hook_and_request_adapter(response):
            url = (
                "https://graph.microsoft.com/v1.0/me/drive/items/1b30fecf-4330-4899-b249-104c2afaf9ed/content"
            )
            trigger = MSGraphTrigger(url, response_type="bytes", conn_id="msgraph_api")
            actual = next(iter(run_trigger(trigger)))

            assert isinstance(actual, TriggerEvent)
            assert actual.payload["status"] == "success"
            assert actual.payload["type"] == "builtins.bytes"
            assert isinstance(actual.payload["response"], str)
            assert actual.payload["response"] == base64_encoded_content

    def test_serialize(self):
        with patch(
            f"{BASEHOOK_PATCH_PATH}.get_connection",
            side_effect=get_airflow_connection,
        ):
            url = "https://graph.microsoft.com/v1.0/me/drive/items"
            trigger = MSGraphTrigger(url, response_type="bytes", conn_id="msgraph_api")

            actual = trigger.serialize()

            assert isinstance(actual, tuple)
            assert actual[0] == f"{MSGraphTrigger.__module__}.{MSGraphTrigger.__name__}"
            assert actual[1] == {
                "url": "https://graph.microsoft.com/v1.0/me/drive/items",
                "path_parameters": None,
                "url_template": None,
                "method": "GET",
                "query_parameters": None,
                "headers": None,
                "data": None,
                "response_type": "bytes",
                "conn_id": "msgraph_api",
                "timeout": None,
                "proxies": None,
                "scopes": [KiotaRequestAdapterHook.DEFAULT_SCOPE],
                "api_version": APIVersion.v1.value,
                "serializer": f"{ResponseSerializer.__module__}.{ResponseSerializer.__name__}",
            }

    def test_template_fields(self):
        trigger = MSGraphTrigger("users/delta", response_type="bytes", conn_id="msgraph_api")

        for template_field in MSGraphTrigger.template_fields:
            getattr(trigger, template_field)


class TestResponseSerializer:
    def test_serialize_when_bytes_then_base64_encoded(self):
        response = load_file_from_resources(
            dirname(__file__), "..", "resources", "dummy.pdf", mode="rb", encoding=None
        )
        content = b64encode(response).decode(locale.getpreferredencoding())

        actual = ResponseSerializer().serialize(response)

        assert isinstance(actual, str)
        assert actual == content

    def test_serialize_when_dict_with_uuid_datatime_and_pendulum_then_json(self):
        id = uuid4()
        response = {
            "id": id,
            "creationDate": datetime(2024, 2, 5),
            "modificationTime": pendulum.datetime(2024, 2, 5),
        }

        actual = ResponseSerializer().serialize(response)

        assert isinstance(actual, str)
        assert (
            actual
            == f'{{"id": "{id}", "creationDate": "2024-02-05T00:00:00", "modificationTime": "2024-02-05T00:00:00+00:00"}}'
        )

    def test_deserialize_when_json(self):
        response = load_file_from_resources(dirname(__file__), "..", "resources", "users.json")

        actual = ResponseSerializer().deserialize(response)

        assert isinstance(actual, dict)
        assert actual == load_json_from_resources(dirname(__file__), "..", "resources", "users.json")

    def test_deserialize_when_base64_encoded_string(self):
        content = load_file_from_resources(
            dirname(__file__), "..", "resources", "dummy.pdf", mode="rb", encoding=None
        )
        response = b64encode(content).decode(locale.getpreferredencoding())

        actual = ResponseSerializer().deserialize(response)

        assert actual == response
        assert b64decode(actual) == content
