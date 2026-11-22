# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from plane.utils.agent_runtime import extract_json


def test_extract_json_plain_object():
    assert extract_json('{"passed": true}') == {"passed": True}


def test_extract_json_strips_code_fences_and_prose():
    text = 'Here is the result:\n```json\n{"tasks": [{"title": "One"}]}\n```\nDone.'
    assert extract_json(text) == {"tasks": [{"title": "One"}]}


def test_extract_json_returns_none_when_no_object():
    assert extract_json("no json here") is None
    assert extract_json("{broken") is None
