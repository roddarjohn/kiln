"""End-to-end coverage of the ``pre`` / ``post`` operation hooks.

Generates a project that wires both hooks on ``create`` and
``update`` and asserts the rendered handler:

* imports the hook callables by their bare name from the
  configured dotted-path module,
* awaits the pre-hook between body parse and the SQL write so
  its return value flows into ``insert(...).values(...)`` /
  ``update(...).values(...)``,
* captures the just-written row via ``.returning(Model)`` so
  the post-hook receives the materialised obj,
* awaits the post-hook after ``db.commit()``.

The handler ``await``s the post-hook directly, so a hook body
is free to spawn an ``asyncio.create_task(...)`` for
fire-and-forget side effects (audit log, downstream
notification, cache bust): the task is created inside the
running event loop and stays alive as long as the hook keeps a
strong reference to it.
"""

from __future__ import annotations

import ast
import asyncio
from typing import TYPE_CHECKING

import pytest

from be.config.schema import (
    App,
    AppConfig,
    DatabaseConfig,
    OperationConfig,
    ProjectConfig,
    ResourceConfig,
)
from be.target import target
from foundry.pipeline import generate

if TYPE_CHECKING:
    from foundry.spec import GeneratedFile


@pytest.fixture
def files() -> dict[str, str]:
    """Generate a project that exercises both hooks on both write ops."""
    config = ProjectConfig(
        databases=[DatabaseConfig(key="primary", default=True)],
        apps=[
            App(
                config=AppConfig(
                    module="blog",
                    resources=[
                        ResourceConfig(
                            model="blog.models.Post",
                            require_auth=False,
                            operations=[
                                OperationConfig(
                                    name="create",
                                    fields=[{"name": "title", "type": "str"}],
                                    pre="blog.hooks.before_create",
                                    post="blog.hooks.after_create",
                                ),
                                OperationConfig(
                                    name="update",
                                    fields=[{"name": "title", "type": "str"}],
                                    pre="blog.hooks.before_update",
                                    post="blog.hooks.after_update",
                                ),
                            ],
                        ),
                    ],
                ),
                prefix="/blog",
            ),
        ],
    )
    rendered: list[GeneratedFile] = generate(config, target)
    return {f.path: f.content for f in rendered}


def test_generated_route_file_parses(files: dict[str, str]) -> None:
    """Hook wiring must not break syntax."""
    ast.parse(files["blog/routes/post.py"])


def test_hook_imports_emitted(files: dict[str, str]) -> None:
    """Each hook is imported by its bare name from the configured module."""
    routes = files["blog/routes/post.py"]
    assert "from blog.hooks import" in routes

    for name in (
        "before_create",
        "after_create",
        "before_update",
        "after_update",
    ):
        assert name in routes


def _handler_block(routes: str, fn_signature: str) -> str:
    """Slice the routes file to one handler's body.

    Stops at the next ``@router`` decorator or end-of-file so the
    last handler in the file is sliced cleanly.
    """
    start = routes.index(fn_signature)
    rest = routes[start:]
    sentinel = rest.find("\n@router", 1)
    return rest if sentinel == -1 else rest[:sentinel]


def test_create_handler_awaits_pre_then_post(files: dict[str, str]) -> None:
    """Pre runs before insert; post runs after commit on the obj."""
    routes = files["blog/routes/post.py"]
    block = _handler_block(routes, "async def create_post(")
    pre = block.index("body = await before_create(body, db=db)")
    insert = block.index("insert(Post)")
    returning = block.index(".returning(Post)")
    commit = block.index("await db.commit()")
    post = block.index("await after_create(obj, body, db=db)")
    assert pre < insert < returning < commit < post


def test_update_handler_awaits_pre_then_post(files: dict[str, str]) -> None:
    """Pre runs before update; post runs after commit on the obj."""
    routes = files["blog/routes/post.py"]
    block = _handler_block(routes, "async def update_post(")
    pre = block.index("body = await before_update(body, db=db)")
    update = block.index("update(Post)")
    returning = block.index(".returning(Post)")
    commit = block.index("await db.commit()")
    post = block.index("await after_update(obj, body, db=db)")
    assert pre < update < returning < commit < post


@pytest.fixture
def dump_files() -> dict[str, str]:
    """Generate a project that overrides the body→values transform.

    Same shape as ``files`` but wires :attr:`OperationConfig.dump`
    instead of pre/post so the assertions can isolate the dump
    path from the surrounding hook plumbing.
    """
    config = ProjectConfig(
        databases=[DatabaseConfig(key="primary", default=True)],
        apps=[
            App(
                config=AppConfig(
                    module="blog",
                    resources=[
                        ResourceConfig(
                            model="blog.models.Post",
                            require_auth=False,
                            operations=[
                                OperationConfig(
                                    name="create",
                                    fields=[{"name": "title", "type": "str"}],
                                    dump="blog.hooks.dump_create",
                                ),
                                OperationConfig(
                                    name="update",
                                    fields=[{"name": "title", "type": "str"}],
                                    dump="blog.hooks.dump_update",
                                ),
                            ],
                        ),
                    ],
                ),
                prefix="/blog",
            ),
        ],
    )
    rendered: list[GeneratedFile] = generate(config, target)
    return {f.path: f.content for f in rendered}


def test_dump_hook_replaces_model_dump_on_create(
    dump_files: dict[str, str],
) -> None:
    """When ``dump`` is set, create's insert calls the user fn instead."""
    routes = dump_files["blog/routes/post.py"]
    block = _handler_block(routes, "async def create_post(")
    assert "**dump_create(body)" in block
    assert "body.model_dump(" not in block


def test_dump_hook_replaces_model_dump_on_update(
    dump_files: dict[str, str],
) -> None:
    """When ``dump`` is set, update's values come from the user fn."""
    routes = dump_files["blog/routes/post.py"]
    block = _handler_block(routes, "async def update_post(")
    assert "**dump_update(body)" in block
    assert "body.model_dump(" not in block


def test_dump_hook_imported_by_bare_name(
    dump_files: dict[str, str],
) -> None:
    """The dump callable is imported from its dotted module."""
    routes = dump_files["blog/routes/post.py"]
    assert "from blog.hooks import" in routes
    assert "dump_create" in routes
    assert "dump_update" in routes


def test_default_dump_keeps_model_dump_call() -> None:
    """No ``dump`` set → templates still spell ``body.model_dump()``."""
    config = ProjectConfig(
        databases=[DatabaseConfig(key="primary", default=True)],
        apps=[
            App(
                config=AppConfig(
                    module="blog",
                    resources=[
                        ResourceConfig(
                            model="blog.models.Post",
                            require_auth=False,
                            operations=[
                                OperationConfig(
                                    name="create",
                                    fields=[{"name": "title", "type": "str"}],
                                ),
                                OperationConfig(
                                    name="update",
                                    fields=[{"name": "title", "type": "str"}],
                                ),
                            ],
                        ),
                    ],
                ),
                prefix="/blog",
            ),
        ],
    )
    rendered = {f.path: f.content for f in generate(config, target)}
    routes = rendered["blog/routes/post.py"]
    assert "body.model_dump()" in routes
    assert "body.model_dump(exclude_unset=True)" in routes


def test_post_hook_can_spawn_background_task() -> None:
    """A post-hook that fires an ``asyncio.create_task`` works end-to-end.

    The generated handler ``await``s the hook, so anything the
    hook body schedules runs on the same event loop.  The hook
    must hold a strong reference to its task -- otherwise the
    GC may drop it before it gets a chance to run.  This test
    documents that contract.
    """
    spawned: list[str] = []
    task_ref: list[asyncio.Task[None]] = []

    async def _background() -> None:
        await asyncio.sleep(0)
        spawned.append("ran")

    async def after_create(_obj: object, _body: object, *, db: object) -> None:
        del db  # signature mirrors the generated call
        task_ref.append(asyncio.create_task(_background()))

    async def scenario() -> None:
        await after_create(object(), object(), db=object())
        await task_ref[0]

    asyncio.run(scenario())
    assert spawned == ["ran"]
