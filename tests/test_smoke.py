from diagfw import test, DUT

def test_smoke_run_local():
    d = DUT(host="localhost", transport="local")
    res = (test("smoke").affects("sys").cmd("python -c 'print(42)'", timeout=5) @ d).run()
    assert res.rc == 0
    assert res.status in ("pass", "fail")
