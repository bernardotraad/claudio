"""Rewriting a description and putting names on people across logs."""
import json

from claudio import core


def _idx(tmp_path):
    return {'cache': str(tmp_path), 'info': {'fps': '24', 'duration': 60, 'start_tc': None}}


def test_replace_and_relabel(tmp_path):
    idx = _idx(tmp_path)
    core.annotate(idx, 0, 30, 'violonista de jaqueta vermelha canta', ['Violonista de jaqueta vermelha'])
    core.annotate(idx, 30, 60, 'plateia')
    core.annotate(idx, 0, 30, 'Rayo canta, close', replace=True)
    L = core.read_log(idx)
    assert [l['text'] for l in L] == ['Rayo canta, close', 'plateia']
    core.annotate(idx, 0, 30, 'guitarrista de camisa xadrez e violonista de jaqueta vermelha', ['guitarrista'], replace=True)
    n = core.relabel_log(idx, {'violonista de jaqueta vermelha': 'Rayo', 'guitarrista de camisa xadrez': 'Izzy', 'guitarrista': 'Izzy'})
    L = core.read_log(idx)
    assert n == 1 and L[0]['text'] == 'Izzy e Rayo' and L[0]['tags'] == ['Izzy']
