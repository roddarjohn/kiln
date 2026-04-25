// kiln stdlib — pgqueuer config helper
// Usage: local queue = import 'kiln/queue/pgqueuer.libsonnet';
//        queue.pgqueuer({
//          tasks_module: "blog.queue.tasks",
//          // database: "primary",  // optional, defaults to default db
//        })
//
// Tasks themselves live in user code: write a Python module of
// `@task`-decorated coroutine functions (from `ingot import task`)
// and point `tasks_module` at it.  Per-task tuning
// (concurrency_limit, retry_timer_seconds, requests_per_second,
// serialized_dispatch) goes on the decorator, not here.
//
// The generated worker imports `tasks_module`, instantiates a
// pgqueuer.PgQueuer, and calls `ingot.register_module_tasks` to
// wire every tagged function as an entrypoint.  Producers call
// `ingot.get_queue(session)` from inside action handlers to enqueue
// jobs in the request's transaction (transactional outbox).
//
// The pgqueuer schema is not managed by kiln — run `pgq install`
// once per database before starting the worker.
{
  pgqueuer(opts):: {
    // Dotted path to the module of @task-decorated coroutines.
    tasks_module: opts.tasks_module,
    // Optional db key.  Omit to bind to the database marked default=true.
    [if std.objectHas(opts, "database") then "database"]: opts.database,
  },
}
