// kiln stdlib — field definition helpers
// Usage: local field = import 'kiln/models/fields.libsonnet';
//        field.uuid("id", primary_key=true)
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
