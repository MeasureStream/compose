-- Schema e utente di metrology-service. Si esegue UNA VOLTA SOLA per database, da un utente
-- con i privilegi di creare ruoli (sulla Pi: POSTGRES_USER del container postgres).
--
-- La password non sta in questo file, che e' in un repository pubblico: la passa psql come
-- variabile, e nello stesso valore va messa in METROLOGY_DB_PASSWORD nel .env del compose.
--
--   docker exec -i postgres psql -v ON_ERROR_STOP=1 \
--       -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
--       -v metrology_password="$METROLOGY_DB_PASSWORD" < 01_schema_e_utente.sql
--
-- Perche' un utente proprio [S §5.2, principio 6 di S §1]: metrology-service non deve poter
-- leggere ne' scrivere le tabelle di sensor-manager. Non basta che il codice non lo faccia:
-- deve essere il database a impedirlo, cosi' un errore di mappatura JPA diventa un'eccezione
-- all'avvio invece di una colonna creata in silenzio (criticita' C2 del lavoro di agosto).

CREATE ROLE metrology LOGIN PASSWORD :'metrology_password';

-- Lo schema appartiene all'utente del servizio: Flyway ci crea le tabelle senza altri GRANT.
CREATE SCHEMA metrology AUTHORIZATION metrology;

-- Le query non qualificate cercano solo nel proprio schema.
ALTER ROLE metrology SET search_path = metrology;

-- Sulle tabelle di public l'utente metrology non ha alcun privilegio: non gli viene concesso
-- niente, e le tabelle appartengono a POSTGRES_USER. Lo si puo' verificare cosi', e deve
-- rispondere "permission denied":
--
--   docker exec -it postgres psql -U metrology -d "$POSTGRES_DB" -c 'SELECT 1 FROM public.sensor LIMIT 1'
