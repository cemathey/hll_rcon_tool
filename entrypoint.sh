#!/usr/bin/env bash

set -e
set -x

env
# Only run the database migrations in the maintenance container
if [ "$1" == 'maintenance' ]
then
  alembic upgrade head
  # merge_duplicate_player_ids / convert_win_player_ids removed from here:
  # both were one-time historical cleanups (old-style Windows Store player
  # IDs, and pre-unique-constraint duplicate rows) that ran unconditionally
  # on every maintenance boot forever. steam_id_64.steam_id_64 now has a
  # live unique index, so merge_duplicate_player_ids can never find
  # anything again; a fleet sample (several containers, including the
  # oldest known install) found zero remaining old-style IDs for
  # convert_win_player_ids. Both were full/near-full PlayerID table scans
  # on every boot — pure, and growing, dead weight. Still available to run
  # manually via `python -m rcon.cli <name>` if ever needed.
  # Upgrade durable Redis structures before any server-specific container starts.
  # The command discovers every populated logical Redis database so multi-server
  # installations are migrated in the same maintenance pass.
  SERVER_NUMBER=1 LOGGING_PATH=/logs/ LOGGING_FILENAME=startup.log python -m rcon.cache_migrations
  cd rconweb
  # Migrations are committed to the repo — makemigrations here would only
  # ever be a no-op scan against the same commit's own models, at the cost
  # of a full Django app-load + model-diff on every boot, and would
  # silently generate+apply an unreviewed migration straight to a live
  # customer DB in the one case where it wasn't a no-op (a model changed
  # without its migration being committed).
  ./manage.py migrate --noinput
  # Run once here instead of once per backend_N: every slot shares the same
  # ./static bind mount and the same image, so N slots previously meant N
  # redundant copies of the same few hundred static files onto the same
  # destination. maintenance already gates every backend_N via
  # `depends_on: condition: service_healthy`, so this is guaranteed done
  # first.
  ./manage.py collectstatic --noinput
  # Create this file after migrations which is how Docker determines the container is healthy
  touch maintenance-container-healthy
  # Keep the container running until it's explicitly created again
  sleep infinity
fi

# Check if we're in the webhook_service container
if [ "$1" == 'webhook_service' ]
then
  LOGGING_PATH=/logs/ LOGGING_FILENAME=webhook_service.log python -m rcon.webhook_service
fi

# Check if we're in a backend container
if [ "$1" == 'web' ]
then
  # If the database password isn't set, bail early
  if [ "$HLL_DB_PASSWORD" == '' ]
  then
      echo "HLL_DB_PASSWORD not set"
      exit 0
  fi
  if [ "$HLL_HOST" == '' ]
  then
      exit 0
  fi

  python -m rcon.user_config.seed_db
  SERVER_NUMBER=${SERVER_NUMBER} LOGGING_PATH=/logs/ LOGGING_FILENAME=api_${SERVER_NUMBER}.log python -m rcon.cli remove_orphaned_map_ids
  SERVER_NUMBER=${SERVER_NUMBER} LOGGING_PATH=/logs/ LOGGING_FILENAME=api_${SERVER_NUMBER}.log python -m rcon.cli clear_maps_cache
  ./manage.py register_api
  cd rconweb
  # collectstatic now runs once in the maintenance container, not per-slot
  # here — see that branch above for why.
  # If DONT_SEED_ADMIN_USER is not set to any value
  if [[ -z "$DONT_SEED_ADMIN_USER" ]]
  then
    echo "from django.contrib.auth.models import User; User.objects.create_superuser('admin', 'admin@example.com', 'admin') if not User.objects.filter(username='admin').first() else None" | python manage.py shell
  fi
  export LOGGING_FILENAME=api_$SERVER_NUMBER.log
  daphne -b 0.0.0.0 -p 8001 rconweb.asgi:application &
  # Successfully running gunicorn will create the pid file which is how Docker determines the container is healthy
  gunicorn --preload --pid gunicorn.pid -w $NB_API_WORKERS -k gthread --threads $NB_API_THREADS -t 120 -b 0.0.0.0 rconweb.wsgi
  cd ..
  ./manage.py unregister_api
else
if [ "$1" == 'debug' ]
then
  tail -f manage.py
fi
if [ "$HLL_HOST" == '' ]
then
    exit 0
fi
  # No sleep here: supervisor_N already `depends_on: backend_N: condition:
  # service_healthy` in compose, so backend_N's gunicorn pid file (its
  # healthcheck) is confirmed present before this container even starts.
  env >> /etc/environment
  export LOGGING_FILENAME=supervisor_$SERVER_NUMBER.log
  supervisord -c /config/supervisord_$SERVER_NUMBER.conf || supervisord -c /config/supervisord.conf
fi
