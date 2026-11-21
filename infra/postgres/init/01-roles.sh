#!/bin/sh
set -eu
psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=POSTGRES_DB="$POSTGRES_DB" --set=finance_password="$FINANCE_DB_PASSWORD" --set=auth_password="$AUTH_DB_PASSWORD" --file /opt/horizon-init/roles.sql
