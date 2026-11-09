from __future__ import annotations

from worker.worker import load_prompt, render_prompt


def test_prompt_keeps_json_braces_and_substitutes_command():
    template = 'Reply like {"intent": "alarm_set"}\nCommand: {command}\nJSON:'
    rendered = render_prompt(template, 'set an alarm')
    assert '{"intent": "alarm_set"}' in rendered
    assert 'Command: set an alarm' in rendered
    assert "{command}" not in rendered


def test_default_prompt_is_valid_and_requests_four_keys():
    text = load_prompt("prompts/teacher_prompt.txt")
    assert "{command}" in text
    for key in ("intent", "date", "time", "place"):
        assert key in text
