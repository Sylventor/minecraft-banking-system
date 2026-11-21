SELECT format('CREATE ROLE horizon_finance LOGIN PASSWORD %L', :'finance_password')
WHERE NOT EXISTS(SELECT FROM pg_roles WHERE rolname='horizon_finance') \gexec
SELECT format('CREATE ROLE horizon_auth LOGIN PASSWORD %L', :'auth_password')
WHERE NOT EXISTS(SELECT FROM pg_roles WHERE rolname='horizon_auth') \gexec
REVOKE ALL ON DATABASE :"POSTGRES_DB" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"POSTGRES_DB" TO horizon_finance, horizon_auth;
CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION horizon_auth;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS finance AUTHORIZATION horizon_finance;
