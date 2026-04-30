"""Tests for the comms config + CommsScaffold operation."""

import ast
import json
from pathlib import Path

import _jsonnet
import pytest
from pydantic import ValidationError

from be.config.schema import (
    CommsConfig,
    CommTypeConfig,
    DatabaseConfig,
    ProjectConfig,
    TemplateSource,
)
from be.operations.comms_scaffold import (
    CommsScaffold,
    _resolve_template,
)
from foundry.engine import BuildContext
from foundry.env import create_jinja_env, render_template
from foundry.jsonnet import make_import_callback
from foundry.outputs import StaticFile
from foundry.scope import PROJECT, ScopeTree
from foundry.store import BuildStore

_BE_JSONNET_DIR = Path(__file__).resolve().parents[2] / "src" / "be" / "jsonnet"

# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestCommTypeConfigDefaults:
    def test_required_fields(self):
        with pytest.raises(ValidationError):
            CommTypeConfig(name="x")  # missing context_schema, body_template

    def test_minimal_valid(self):
        ct = CommTypeConfig(
            name="welcome",
            context_schema="myapp.comms.WelcomeCtx",
            body_template="Hi {{ name }}",
        )
        assert ct.subject_template == ""
        assert ct.default_methods == []


class TestCommsConfigDefaults:
    def test_required_fields(self):
        with pytest.raises(ValidationError):
            CommsConfig()  # missing message_model + recipient_model

    def test_minimal_valid(self):
        cfg = CommsConfig(
            message_model="myapp.models.Msg",
            recipient_model="myapp.models.Rcp",
        )
        assert cfg.types == []
        assert cfg.transports == {}
        assert cfg.renderer is None
        assert cfg.preferences is None
        assert cfg.db_key is None

    def test_unique_type_names_enforced(self):
        with pytest.raises(ValidationError, match="unique names"):
            CommsConfig(
                message_model="m.M",
                recipient_model="m.R",
                types=[
                    CommTypeConfig(
                        name="dup",
                        context_schema="m.A",
                        body_template="a",
                    ),
                    CommTypeConfig(
                        name="dup",
                        context_schema="m.B",
                        body_template="b",
                    ),
                ],
            )


class TestProjectConfigComms:
    def test_comms_defaults_none(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
        )
        assert cfg.comms is None

    def test_comms_attached(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            comms=CommsConfig(
                message_model="myapp.models.Msg",
                recipient_model="myapp.models.Rcp",
            ),
        )
        assert cfg.comms is not None
        assert cfg.comms.message_model == "myapp.models.Msg"

    def test_db_key_must_resolve(self):
        with pytest.raises(ValidationError, match="missing"):
            ProjectConfig(
                databases=[DatabaseConfig(key="primary", default=True)],
                comms=CommsConfig(
                    message_model="m.M",
                    recipient_model="m.R",
                    db_key="missing",
                ),
            )

    def test_db_key_default_resolves(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            comms=CommsConfig(
                message_model="m.M",
                recipient_model="m.R",
            ),
        )
        # No exception means the default-DB lookup worked.
        assert cfg.comms is not None


# ---------------------------------------------------------------------------
# TemplateSource + _resolve_template
# ---------------------------------------------------------------------------


class TestTemplateSource:
    def test_pydantic_accepts_inline_string(self):
        ct = CommTypeConfig(
            name="x",
            context_schema="m.M",
            subject_template="Subj",
            body_template="Body",
        )
        assert ct.body_template == "Body"

    def test_pydantic_accepts_path_dict(self):
        ct = CommTypeConfig(
            name="x",
            context_schema="m.M",
            body_template={"path": "templates/body.html"},
        )
        assert isinstance(ct.body_template, TemplateSource)
        assert ct.body_template.path == "templates/body.html"

    def test_template_source_rejects_extra_keys(self):
        with pytest.raises(ValidationError):
            CommTypeConfig(
                name="x",
                context_schema="m.M",
                body_template={"path": "f", "junk": True},
            )

    def test_resolve_inline_string_passes_through(self):
        out = _resolve_template("Hi {{ name }}", kind="body", name="welcome")
        assert out == "Hi {{ name }}"

    def test_resolve_path_reads_file(self, tmp_path: Path):
        body = tmp_path / "body.txt"
        body.write_text("Welcome {{ name }}!", encoding="utf-8")
        out = _resolve_template(
            TemplateSource(path=str(body)),
            kind="body",
            name="welcome",
        )
        assert out == "Welcome {{ name }}!"

    def test_resolve_path_missing_raises_with_context(self, tmp_path: Path):
        missing = tmp_path / "no.html"

        with pytest.raises(FileNotFoundError, match=r"welcome.*body"):
            _resolve_template(
                TemplateSource(path=str(missing)),
                kind="body",
                name="welcome",
            )


class TestScaffoldFileTemplates:
    def _build(self, cfg: ProjectConfig) -> StaticFile:
        return next(
            iter(
                CommsScaffold().build(
                    BuildContext(
                        config=cfg,
                        scope=PROJECT,
                        instance=cfg,
                        instance_id="project",
                        store=BuildStore(scope_tree=ScopeTree([PROJECT])),
                    ),
                    _options=CommsScaffold().Options(),
                ),
            ),
        )

    def test_inlines_file_contents_at_build_time(self, tmp_path: Path):
        body_file = tmp_path / "order.html"
        body_file.write_text(
            "<h1>Order {{ order_id }}</h1>",
            encoding="utf-8",
        )
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            comms=CommsConfig(
                message_model="m.M",
                recipient_model="m.R",
                types=[
                    CommTypeConfig(
                        name="order_shipped",
                        context_schema="m.OrderCtx",
                        subject_template="Order shipped",
                        body_template=TemplateSource(path=str(body_file)),
                    ),
                ],
            ),
        )
        out = self._build(cfg)
        rendered_type = out.context["types"][0]
        assert rendered_type["body_template"] == "<h1>Order {{ order_id }}</h1>"
        # Subject is inline; passes through unchanged.
        assert rendered_type["subject_template"] == "Order shipped"

    def test_missing_file_surfaces_clear_error(self, tmp_path: Path):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            comms=CommsConfig(
                message_model="m.M",
                recipient_model="m.R",
                types=[
                    CommTypeConfig(
                        name="welcome",
                        context_schema="m.WelcomeCtx",
                        body_template=TemplateSource(
                            path=str(tmp_path / "missing.html"),
                        ),
                    ),
                ],
            ),
        )

        with pytest.raises(FileNotFoundError, match=r"welcome.*body"):
            self._build(cfg)


# ---------------------------------------------------------------------------
# Jsonnet stdlib helper
# ---------------------------------------------------------------------------


class TestCommsLibsonnet:
    """Evaluate the libsonnet helper through the same ``_jsonnet``
    binding foundry uses at generate time.

    The helper's output must shape-match
    :class:`~be.config.schema.CommsConfig` so the project config it
    produces validates without further glue.
    """

    def _eval(self, src: str) -> dict:
        json_str = _jsonnet.evaluate_snippet(
            "test.jsonnet",
            src,
            import_callback=make_import_callback({"be": _BE_JSONNET_DIR}),
        )
        return json.loads(json_str)

    def test_platform_minimal_emits_required_fields(self):
        out = self._eval(
            """
            local c = import 'be/comms/comms.libsonnet';
            c.platform({
              message_model: 'm.M',
              recipient_model: 'm.R',
            })
            """,
        )
        # Optional keys are omitted, not null -- so absent keys
        # inherit Pydantic defaults.
        assert out == {
            "message_model": "m.M",
            "recipient_model": "m.R",
            "types": [],
            "transports": {},
        }

    def test_platform_passes_through_optional_overrides(self):
        out = self._eval(
            """
            local c = import 'be/comms/comms.libsonnet';
            c.platform({
              message_model: 'm.M',
              recipient_model: 'm.R',
              transports: { email: 'm.t.email' },
              renderer: 'm.r.node',
              preferences: 'm.p.resolver',
              db_key: 'audit',
            })
            """,
        )
        assert out["transports"] == {"email": "m.t.email"}
        assert out["renderer"] == "m.r.node"
        assert out["preferences"] == "m.p.resolver"
        assert out["db_key"] == "audit"

    def test_type_helper_minimal(self):
        out = self._eval(
            """
            local c = import 'be/comms/comms.libsonnet';
            c.type({
              name: 'welcome',
              context_schema: 'm.W',
              body_template: 'Hi',
            })
            """,
        )
        assert out == {
            "name": "welcome",
            "context_schema": "m.W",
            "body_template": "Hi",
        }

    def test_path_helper_emits_template_source_shape(self):
        out = self._eval(
            """
            local c = import 'be/comms/comms.libsonnet';
            c.path('templates/x.html')
            """,
        )
        assert out == {"path": "templates/x.html"}

    def test_path_helper_round_trips_through_pydantic(self):
        # The dict shape produced by `comms.path(...)` must validate
        # cleanly as the body_template of a CommTypeConfig -- this
        # is the contract that ties the libsonnet helper to the
        # Python schema.
        out = self._eval(
            """
            local c = import 'be/comms/comms.libsonnet';
            c.type({
              name: 't',
              context_schema: 'm.M',
              body_template: c.path('templates/body.html'),
            })
            """,
        )
        ct = CommTypeConfig.model_validate(out)
        assert isinstance(ct.body_template, TemplateSource)
        assert ct.body_template.path == "templates/body.html"

    def test_full_platform_validates_as_comms_config(self):
        out = self._eval(
            """
            local c = import 'be/comms/comms.libsonnet';
            c.platform({
              message_model: 'm.M',
              recipient_model: 'm.R',
              transports: { email: 'm.t.email' },
              types: [
                c.type({
                  name: 'welcome',
                  context_schema: 'm.W',
                  subject_template: 'Welcome',
                  body_template: c.path('templates/welcome.html'),
                  default_methods: ['email'],
                }),
              ],
            })
            """,
        )
        cfg = CommsConfig.model_validate(out)
        assert len(cfg.types) == 1
        assert cfg.types[0].name == "welcome"
        assert isinstance(cfg.types[0].body_template, TemplateSource)


# ---------------------------------------------------------------------------
# CommsScaffold operation
# ---------------------------------------------------------------------------


_SCOPE_TREE_PROJECT = ScopeTree([PROJECT])


def _project_ctx(config: ProjectConfig) -> BuildContext:
    return BuildContext(
        config=config,
        scope=PROJECT,
        instance=config,
        instance_id="project",
        store=BuildStore(scope_tree=_SCOPE_TREE_PROJECT),
    )


class TestCommsScaffoldGate:
    def test_when_off_without_comms(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
        )
        assert CommsScaffold().when(_project_ctx(cfg)) is False

    def test_when_on_with_comms(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            comms=CommsConfig(
                message_model="m.M",
                recipient_model="m.R",
            ),
        )
        assert CommsScaffold().when(_project_ctx(cfg)) is True


class TestCommsScaffoldOutputs:
    def _build(self, cfg: ProjectConfig) -> list[StaticFile]:
        return list(
            CommsScaffold().build(
                _project_ctx(cfg),
                _options=CommsScaffold().Options(),
            ),
        )

    def _cfg(self, **comms_kwargs) -> ProjectConfig:
        defaults = {
            "message_model": "myapp.models.Msg",
            "recipient_model": "myapp.models.Rcp",
        }
        defaults.update(comms_kwargs)
        return ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            comms=CommsConfig(**defaults),
        )

    def test_emits_single_comms_file(self):
        outputs = self._build(self._cfg())
        assert all(isinstance(o, StaticFile) for o in outputs)
        assert {o.path for o in outputs} == {"comms.py"}

    def test_message_and_recipient_modules_split(self):
        ctx = self._build(self._cfg())[0].context
        assert ctx["message_module"] == "myapp.models"
        assert ctx["message_class"] == "Msg"
        assert ctx["recipient_module"] == "myapp.models"
        assert ctx["recipient_class"] == "Rcp"

    def test_types_resolved_with_split_imports(self):
        ctx = self._build(
            self._cfg(
                types=[
                    CommTypeConfig(
                        name="welcome",
                        context_schema="myapp.comms.WelcomeCtx",
                        subject_template="Welcome {{ name }}",
                        body_template="Hi {{ name }}",
                        default_methods=["email", "sms"],
                    ),
                ],
            ),
        )[0].context
        assert ctx["types"] == [
            {
                "name": "welcome",
                "context_module": "myapp.comms",
                "context_class": "WelcomeCtx",
                "subject_template": "Welcome {{ name }}",
                "body_template": "Hi {{ name }}",
                "default_methods": ["email", "sms"],
            },
        ]

    def test_transports_resolved(self):
        ctx = self._build(
            self._cfg(
                transports={
                    "email": "myapp.comms.transports.email_transport",
                    "sms": "myapp.comms.transports.sms_transport",
                },
            ),
        )[0].context
        assert ctx["transports"] == [
            {
                "method": "email",
                "module": "myapp.comms.transports",
                "name": "email_transport",
            },
            {
                "method": "sms",
                "module": "myapp.comms.transports",
                "name": "sms_transport",
            },
        ]

    def test_renderer_dotted_path_split(self):
        ctx = self._build(
            self._cfg(renderer="myapp.comms.renderers.node_renderer"),
        )[0].context
        assert ctx["renderer_module"] == "myapp.comms.renderers"
        assert ctx["renderer_name"] == "node_renderer"

    def test_renderer_default_none(self):
        ctx = self._build(self._cfg())[0].context
        assert ctx["renderer_module"] is None
        assert ctx["renderer_name"] is None

    def test_preferences_dotted_path_split(self):
        ctx = self._build(
            self._cfg(preferences="myapp.comms.prefs.resolver"),
        )[0].context
        assert ctx["preferences_module"] == "myapp.comms.prefs"
        assert ctx["preferences_name"] == "resolver"

    def test_session_module_uses_package_prefix(self):
        ctx = self._build(self._cfg())[0].context
        # session_module is `db.{key}_session`; package_prefix is
        # `_generated` by default → `_generated.db.primary_session`.
        assert ctx["session_module"] == "_generated.db.primary_session"
        assert ctx["db_key"] == "primary"

    def test_session_module_omits_empty_prefix(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            package_prefix="",
            comms=CommsConfig(
                message_model="m.M",
                recipient_model="m.R",
            ),
        )
        ctx = self._build(cfg)[0].context
        assert ctx["session_module"] == "db.primary_session"

    def test_comms_module_uses_package_prefix(self):
        ctx = self._build(self._cfg())[0].context
        assert ctx["comms_module"] == "_generated.comms"

    def test_comms_module_omits_empty_prefix(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            package_prefix="",
            comms=CommsConfig(
                message_model="m.M",
                recipient_model="m.R",
            ),
        )
        ctx = self._build(cfg)[0].context
        assert ctx["comms_module"] == "comms"

    def test_template_renders_to_valid_python(self):
        # Smoke test: feed the scaffold's StaticFile context through
        # the same Jinja env be uses at build time and confirm the
        # output parses as Python.  Catches template typos before
        # they reach a real generate run.
        cfg = self._cfg(
            types=[
                CommTypeConfig(
                    name="welcome",
                    context_schema="myapp.comms.WelcomeCtx",
                    subject_template="Welcome",
                    body_template="Hi {{ name }}",
                    default_methods=["email"],
                ),
            ],
            transports={
                "email": "myapp.comms.transports.email_transport",
            },
            renderer="myapp.comms.renderers.node_renderer",
            preferences="myapp.comms.prefs.resolver",
        )
        ctx = self._build(cfg)[0].context

        be_templates = Path("src/be/templates")
        foundry_templates = Path("src/foundry/templates")
        env = create_jinja_env(be_templates, foundry_templates)
        rendered = render_template(env, "init/comms_setup.py.j2", **ctx)
        # Parses cleanly -- catches missing/extra commas and bad
        # indentation in the template.
        ast.parse(rendered)
        # Confirms the template populated its key surfaces.
        assert "registry = CommRegistry()" in rendered
        assert "registry.register(" in rendered
        assert '"welcome"' in rendered
        assert "node_renderer" in rendered
        assert "resolver" in rendered
        assert "dispatch = make_dispatch_entrypoint(" in rendered

    def test_template_renders_with_no_optional_overrides(self):
        # A minimal config (no transports, types, renderer, prefs)
        # must still produce parseable Python.
        cfg = self._cfg()
        ctx = self._build(cfg)[0].context

        env = create_jinja_env(
            Path("src/be/templates"),
            Path("src/foundry/templates"),
        )
        rendered = render_template(env, "init/comms_setup.py.j2", **ctx)
        ast.parse(rendered)
        assert "registry = CommRegistry()" in rendered

    def test_named_database_picked_by_db_key(self):
        cfg = ProjectConfig(
            databases=[
                DatabaseConfig(key="primary", default=True),
                DatabaseConfig(key="audit", url_env="AUDIT_DB_URL"),
            ],
            comms=CommsConfig(
                message_model="m.M",
                recipient_model="m.R",
                db_key="audit",
            ),
        )
        ctx = self._build(cfg)[0].context
        assert ctx["db_key"] == "audit"
        assert ctx["session_module"] == "_generated.db.audit_session"
