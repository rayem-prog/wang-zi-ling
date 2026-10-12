from types import SimpleNamespace
from scripts import app_runner


def test_second_launch_opens_existing_app_without_starting_services(monkeypatch):
    monkeypatch.setattr(app_runner,'parse_args',lambda:SimpleNamespace(host='127.0.0.1',port=8888,no_browser=False,no_daemon=False))
    monkeypatch.setattr(app_runner.os,'chdir',lambda _:None)
    def occupied(_): raise RuntimeError('occupied')
    monkeypatch.setattr(app_runner,'ensure_port_available',occupied)
    monkeypatch.setattr(app_runner,'existing_app_url',lambda *a:'http://127.0.0.1:8888',raising=False)
    opened=[]
    monkeypatch.setattr(app_runner.webbrowser,'open',opened.append)
    monkeypatch.setattr(app_runner.daemon_control,'start_daemon',lambda *a:(_ for _ in ()).throw(AssertionError('second daemon')))
    monkeypatch.setattr(app_runner.uvicorn,'run',lambda *a,**k:(_ for _ in ()).throw(AssertionError('second server')))
    assert app_runner.main()==0
    assert opened==['http://127.0.0.1:8888']


def test_unrelated_occupied_port_reports_error_without_opening_or_killing(monkeypatch,capsys):
    monkeypatch.setattr(app_runner,'parse_args',lambda:SimpleNamespace(host='127.0.0.1',port=8888,no_browser=False,no_daemon=False))
    monkeypatch.setattr(app_runner.os,'chdir',lambda _:None)
    def occupied(_): raise RuntimeError('occupied by unrelated service')
    monkeypatch.setattr(app_runner,'ensure_port_available',occupied)
    monkeypatch.setattr(app_runner,'existing_app_url',lambda *a:None,raising=False)
    monkeypatch.setattr(app_runner.webbrowser,'open',lambda *a:(_ for _ in ()).throw(AssertionError('wrong app')))
    assert app_runner.main()==2
    assert 'unrelated service' in capsys.readouterr().out
