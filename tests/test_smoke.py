import stagecraft


def test_package_imports() -> None:
    assert stagecraft.__version__ == "0.1.0"


def test_subpackages_import() -> None:
    import stagecraft.agents
    import stagecraft.api
    import stagecraft.mcp
    import stagecraft.memory
    import stagecraft.plan
    import stagecraft.tools

    assert stagecraft.plan is not None
