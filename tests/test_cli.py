from mcpgateway_client.foo import foo


def test_foo():
    assert foo("foo") == "foo"
