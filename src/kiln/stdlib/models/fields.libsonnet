// kiln stdlib — field definition helpers
//
// Usage: local field = import 'kiln/models/fields.libsonnet';
//        field.uuid("id", primary_key=true)
//
// primary_key accepts:
//   false                                  — not a primary key (default)
//   true                                   — PK with the default plugin for this type
//   "pgcraft.plugins.pk.UUIDV7PKPlugin"    — PK with a specific plugin (dotted path)
//
// See kiln/pgcraft/plugins.libsonnet for named aliases:
//   local plugins = import 'kiln/pgcraft/plugins.libsonnet';
//   field.uuid("id", primary_key=plugins.pk.uuid_v7)
{
  _base(name, type, extra={}):: { name: name, type: type } + extra,

  uuid(name, primary_key=false, nullable=false, foreign_key=null)::
    self._base(name, "uuid", {
      primary_key: primary_key,
      nullable: nullable,
    } + (if foreign_key != null then { foreign_key: foreign_key } else {})),

  str(name, unique=false, nullable=false, exclude_from_api=false, index=false)::
    self._base(name, "str", {
      unique: unique,
      nullable: nullable,
      exclude_from_api: exclude_from_api,
      index: index,
    }),

  email(name, unique=false, nullable=false)::
    self._base(name, "email", { unique: unique, nullable: nullable }),

  int(name, primary_key=false, nullable=false, foreign_key=null)::
    self._base(name, "int", {
      primary_key: primary_key,
      nullable: nullable,
    } + (if foreign_key != null then { foreign_key: foreign_key } else {})),

  float(name, nullable=false)::
    self._base(name, "float", { nullable: nullable }),

  bool(name, nullable=false)::
    self._base(name, "bool", { nullable: nullable }),

  datetime(name, auto_now_add=false, auto_now=false, nullable=false)::
    self._base(name, "datetime", {
      auto_now_add: auto_now_add,
      auto_now: auto_now,
      nullable: nullable,
    }),

  date(name, nullable=false)::
    self._base(name, "date", { nullable: nullable }),

  json(name, nullable=true)::
    self._base(name, "json", { nullable: nullable }),
}
