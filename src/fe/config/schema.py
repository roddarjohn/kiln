"""Pydantic schema for fe configs.

The fe target generates a React/TypeScript app driven by an
OpenAPI spec.  The config mirrors the be target's shape so users
fluent in one feel at home in the other: a top-level
``ProjectConfig`` carries openapi-ts settings, an optional
``ShellConfig`` (AppShell + nav), an optional ``AuthConfig``
(login page + token storage), and a dict of per-resource
``ResourceConfig`` describing list / form / action UIs.

Every codegen output composes glaze components -- the kiln-side
config picks *which* glaze pieces to wire up against the
openapi-ts-generated SDK.

Operation IDs (e.g. ``listProjectsV1TrackerProjectsSearchPost``)
and TypeScript type names (e.g. ``ProjectListItem``) are spelled
out explicitly here.  They come from the BE's openapi.json and
the openapi-ts-generated ``sdk.gen.ts`` / ``types.gen.ts``.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foundry.config import FoundryConfig

# ---------------------------------------------------------------------------
# AppShell + navigation
# ---------------------------------------------------------------------------


class NavItem(BaseModel):
    """A sidebar nav entry.

    Attributes:
        label: Visible text in the sidebar.
        view: Either a resource key from
            :attr:`ProjectConfig.resources` (renders that
            resource's list page) or an arbitrary identifier
            for a custom view (only meaningful if you've added a
            custom page wired in your own ``App.tsx`` edits).
        icon: Lucide icon name (e.g. ``"FolderOpen"``) rendered
            beside *label*.  When set, the codegen adds the
            matching named import from ``lucide-react`` to
            ``Shell.tsx`` -- the project must have
            ``lucide-react`` installed (it ships in the
            ``fe_root`` bootstrap so any kiln-scaffolded fe app
            already has it).

    """

    model_config = ConfigDict(extra="forbid")

    label: str
    view: str
    icon: str | None = Field(default=None)


class ShellConfig(BaseModel):
    """AppShell + sidebar navigation configuration.

    When omitted from the project config, the codegen emits a
    minimal app with no shell wrapper -- useful for embedded /
    headless integrations.

    Attributes:
        brand: Text shown at the top of the sidebar.
        nav: Sidebar items, in the order they should appear.
        user_menu: Render the avatar + sign-out button at the
            bottom of the sidebar.  Auto-disabled when the
            project has no auth config.

    """

    model_config = ConfigDict(extra="forbid")

    brand: str = Field(default="App")
    nav: list[NavItem] = Field(default_factory=list)
    user_menu: bool = Field(default=True)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AuthConfig(BaseModel):
    """Login page + AuthProvider wiring.

    Attributes:
        storage: Glaze storage backend for the bearer token.
        token_key: localStorage / sessionStorage key under which
            the token is persisted.  Must match the runtime
            value the openapi-ts client looks up; the codegen
            wires both ends to this same key.
        login_fn: openapi-ts SDK function that exchanges
            credentials for a token, e.g.
            ``"createTokenV1AuthTokenPost"``.
        validate_fn: SDK function that returns the current
            session given a valid bearer token.
        logout_fn: SDK function that revokes the session.
        session_type: TS type name for the session object the
            BE returns.
        credentials_type: TS type name for the login request
            body.
        credentials_fields: Field names on the credentials type
            -- order determines the order of the form inputs.
        login_hint: Optional text under the Sign in heading
            (e.g. ``"Try alice / wonderland"`` for demos).

    """

    model_config = ConfigDict(extra="forbid")

    storage: Literal["localStorage", "sessionStorage", "memory", "cookie"] = (
        Field(default="localStorage")
    )
    token_key: str = Field(default="glaze:auth:token")
    login_fn: str
    validate_fn: str
    logout_fn: str
    session_type: str = Field(default="Session")
    credentials_type: str = Field(default="LoginCredentials")
    credentials_fields: list[str] = Field(
        default_factory=lambda: ["username", "password"]
    )
    login_hint: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Resource: list view, forms, actions
# ---------------------------------------------------------------------------


class ColumnSpec(BaseModel):
    """One column in a resource's list view.

    Attributes:
        field: Property name on the list-item type.
        label: Column header text.  Defaults to a Title-Cased
            version of *field*.
        display: How to render cell content.  ``"text"`` (default)
            stringifies the value; ``"badge"`` renders a glaze
            ``Badge`` whose tone is ``"success"`` for truthy
            values and ``"neutral"`` otherwise -- right for
            booleans like ``completed``.
        sortable: When True, the column header is clickable and
            toggles ascending / descending; the active sort is
            sent to the list-fn body's ``sort`` array.  The
            field name must appear in the BE's
            ``{Resource}SortField`` enum (i.e. it must be
            declared sortable on the BE list op).

    """

    model_config = ConfigDict(extra="forbid")

    field: str
    label: str | None = Field(default=None)
    display: Literal["text", "badge"] = Field(default="text")
    sortable: bool = Field(default=False)


FilterOp = Literal[
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "starts_with",
    "in",
]


class FilterSpec(BaseModel):
    """One filter rendered in the list page's FilterBar.

    The filter's value is sent to the list endpoint as a single
    condition ``{field, op, value}``.  The codegen wraps multiple
    active conditions in an ``{and: [...]}`` expression so the BE
    sees them all at once.

    Attributes:
        field: Field name on the list-fn request body's
            ``filter.field`` literal.
        label: Display text on the filter chip.  Defaults to a
            humanized version of *field*.
        type: Control type.

            * ``"text"``: glaze ``<TextField>``; default op is
              ``"contains"``.
            * ``"boolean"``: glaze ``<Switch>``; default op is
              ``"eq"``.
            * ``"select"``: glaze ``<Select>`` with *options*
              entries; default op is ``"eq"``.
        options: Choices for ``"select"`` filters.  Ignored
            otherwise.
        op: Filter operator sent to the BE.  Defaults to a
            sensible per-type value (text -> contains,
            boolean -> eq, select -> eq).

    """

    model_config = ConfigDict(extra="forbid")

    field: str
    label: str | None = Field(default=None)
    type: Literal["text", "boolean", "select"] = Field(default="text")
    options: list[str] = Field(default_factory=list)
    op: FilterOp | None = Field(default=None)


class ListConfig(BaseModel):
    """List-view configuration for a resource.

    Attributes:
        columns: Column definitions, left to right.
        toolbar_actions: Built-in toolbar buttons.  ``"create"``
            renders a ``New {label.singular}`` button that opens
            the resource's create form.
        row_actions: Built-in per-row buttons.  ``"delete"``
            renders a Delete button wired to the resource's
            ``delete_fn``.
        row_click: What clicking a row does.  ``"detail"`` opens
            a Drawer rendering the resource's Detail component
            (requires ``detail`` + ``get_fn`` on the resource).
            ``None`` (default) makes rows non-clickable.
        filters: Filter chips rendered above the table via glaze
            ``FilterBar`` + ``useFilters``.  Active filter values
            are sent to the list-fn as a single condition or an
            ``{and: [...]}`` expression.

    """

    model_config = ConfigDict(extra="forbid")

    columns: list[ColumnSpec] = Field(default_factory=list)
    toolbar_actions: list[Literal["create"]] = Field(default_factory=list)
    row_actions: list[Literal["delete"]] = Field(default_factory=list)
    row_click: Literal["detail"] | None = Field(default=None)
    filters: list[FilterSpec] = Field(default_factory=list)
    page_size: int | None = Field(default=None)


class FormConfig(BaseModel):
    """Create or update form for a resource.

    Attributes:
        fields: Field names on the request body type.
        presentation: How to surface the form.  ``"page"``
            (default) renders a full route page with PageHeader +
            Card; ``"drawer"`` wraps the same content in a glaze
            Drawer that closes via ``history.back()``.  Either
            way the form has a stable URL.

    """

    model_config = ConfigDict(extra="forbid")

    fields: list[str] = Field(default_factory=list)
    presentation: Literal["drawer", "modal", "page"] = Field(default="drawer")


class ActionConfig(BaseModel):
    """A custom (non-CRUD) action on a resource.

    Attributes:
        label: Button text.
        fn: openapi-ts SDK function name.
        presentation: Modal vs Drawer for the form.
        request_schema: TS type name of the request body, if any.
        fields: Field names from the request body to render in
            the form.  Empty list => confirmation-only action
            (no form fields).
        confirm_text: Optional sentence rendered above the
            form (e.g. ``"Mark X as done?"``).
        row_action: When True, surfaces the action as a per-row
            button on the list view (only when *row_action_when*
            evaluates truthy on the row).
        row_action_when: A JS expression on the row item; the
            row button renders only when this is truthy.  E.g.
            ``"!item.completed"``.  Ignored when
            *row_action* is False.

    """

    model_config = ConfigDict(extra="forbid")

    label: str
    fn: str
    presentation: Literal["modal", "drawer"] = Field(default="modal")
    request_schema: str | None = Field(default=None)
    fields: list[str] = Field(default_factory=list)
    confirm_text: str | None = Field(default=None)
    row_action: bool = Field(default=False)
    row_action_when: str | None = Field(default=None)


class DetailSection(BaseModel):
    """One section of a detail view.

    A section is either a ``fields`` list (rendered as
    label/value pairs from the resource type) or a custom
    ``component`` import path that takes the resource as a prop.

    Attributes:
        title: Heading shown above the section.  Optional --
            sections without a title render their content with
            no heading.
        fields: Field names on the resource type.  Mutually
            exclusive with *component*.
        component: TS import path (relative to ``src/``) of a
            user-supplied component receiving the resource as
            ``item``.  Use when the section needs richer
            rendering than label/value pairs.

    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None)
    fields: list[str] = Field(default_factory=list)
    component: str | None = Field(default=None)


class DetailConfig(BaseModel):
    """Detail-view configuration for a resource.

    Attributes:
        sections: Section definitions, top to bottom.
        actions: Action keys (from ``ResourceConfig.actions``)
            to render as buttons in the detail header.
        presentation: How to surface the detail view.  ``"page"``
            (default) renders a full route page; ``"drawer"``
            wraps it in a glaze Drawer that closes via
            ``history.back()``.  The route URL ``/<key>/$id`` is
            stable across both modes.

    """

    model_config = ConfigDict(extra="forbid")

    sections: list[DetailSection] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    presentation: Literal["page", "drawer"] = Field(default="page")


class ResourceLabel(BaseModel):
    """Singular + plural display labels for a resource."""

    model_config = ConfigDict(extra="forbid")

    singular: str
    plural: str


class ResourceConfig(BaseModel):
    """Per-resource UI configuration.

    Each entry maps a resource key (used in URLs and component
    names) to its list / form / action surfaces and the
    openapi-ts symbols that drive them.

    Attributes:
        label: Display labels.
        list_item_type: TS type name returned by ``list_fn``'s
            paginated items.
        resource_type: TS type name returned by ``get_fn`` (the
            full resource representation).  Optional, only
            needed if you wire a detail view.
        list_fn: SDK function for the list endpoint.
        get_fn: SDK function for the get-by-id endpoint.
        create_fn: SDK function for create.
        update_fn: SDK function for update.
        delete_fn: SDK function for delete.
        create_request_type: TS type name for the create body.
        update_request_type: TS type name for the update body.
        list: List-view config.
        create: Create-form config.  Omit to disable.
        update: Update-form config.  Omit to disable.
        actions: Custom actions keyed by action name.

    """

    model_config = ConfigDict(extra="forbid")

    label: ResourceLabel
    list_item_type: str
    resource_type: str | None = Field(default=None)
    list_fn: str | None = Field(default=None)
    get_fn: str | None = Field(default=None)
    create_fn: str | None = Field(default=None)
    update_fn: str | None = Field(default=None)
    delete_fn: str | None = Field(default=None)
    create_request_type: str | None = Field(default=None)
    update_request_type: str | None = Field(default=None)
    list: ListConfig = Field(default_factory=ListConfig)
    detail: DetailConfig | None = Field(default=None)
    create: FormConfig | None = Field(default=None)
    update: FormConfig | None = Field(default=None)
    actions: dict[str, ActionConfig] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------


class ProjectConfig(FoundryConfig):
    """Top-level config for the ``fe`` target.

    Attributes:
        openapi_spec: Path or URL of the OpenAPI 3.x spec
            ``openapi-ts`` should ingest.
        output_dir: Where ``openapi-ts`` writes its generated
            client.
        client: openapi-ts runtime client.
        react_query: Enable the @tanstack/react-query plugin.
        format: Optional formatter for openapi-ts output.
        shell: AppShell + sidebar config.  Omit for a headless
            app with no shell wrapper.
        auth: Login page + AuthProvider config.  Omit when the
            BE has no auth.
        resources: Per-resource UI config, keyed by resource
            name.  Empty dict produces an app with shell + auth
            but no resource pages -- the developer fills them in
            manually.

    """

    model_config = ConfigDict(extra="forbid")

    openapi_spec: str = Field(default="../be/openapi.json")
    output_dir: str = Field(default="src/_generated")
    client: Literal[
        "@hey-api/client-fetch",
        "@hey-api/client-axios",
    ] = Field(default="@hey-api/client-fetch")
    react_query: bool = Field(default=True)
    format: Literal["prettier", "biome"] | None = Field(default=None)

    shell: ShellConfig | None = Field(default=None)
    auth: AuthConfig | None = Field(default=None)
    resources: dict[str, ResourceConfig] = Field(default_factory=dict)
