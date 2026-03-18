-- 014_backfill_geocoding_cities.sql
-- Backfill missing city values for Estonian and Mexican facilities.
--
-- These facilities have empty city AND address fields in the source PDFs,
-- which causes the geocoder (script 07) to skip them entirely.  The city
-- assignments below are based on the known locations of these institutions.
--
-- After applying this migration, re-run:
--   python scripts/07_geocode.py --table facility_years
-- to geocode the ~120 newly-addressable rows (~2 min).

BEGIN;

-- ── Estonia ─────────────────────────────────────────────────────────────────
-- Pattern: almost ALL Estonian rows lack city/address.  The 12 that geocoded
-- already had city='Tartu'.

-- Tartu institutions
UPDATE facility_years SET city = 'Tartu'
WHERE country_iso3 = 'EST' AND (city IS NULL OR city = '')
  AND facility_name LIKE '%Tartu%';

UPDATE facility_years SET city = 'Tartu'
WHERE country_iso3 = 'EST' AND (city IS NULL OR city = '')
  AND facility_name LIKE 'Icosagen Cell Factory%';

UPDATE facility_years SET city = 'Tartu'
WHERE country_iso3 = 'EST' AND (city IS NULL OR city = '')
  AND facility_name LIKE '%Mycobacteriosis%University of Life Sciences%';

UPDATE facility_years SET city = 'Tartu'
WHERE country_iso3 = 'EST' AND (city IS NULL OR city = '')
  AND facility_name LIKE 'Mycobacteriosis Laboratory%';

UPDATE facility_years SET city = 'Tartu'
WHERE country_iso3 = 'EST' AND (city IS NULL OR city = '')
  AND facility_name LIKE '%Laboratory for Mycobacteriosis%'
  AND facility_name NOT LIKE '%Health Board%';

UPDATE facility_years SET city = 'Tartu'
WHERE country_iso3 = 'EST' AND (city IS NULL OR city = '')
  AND facility_name LIKE '%Veterinary%Food Laboratory%';

UPDATE facility_years SET city = 'Tartu'
WHERE country_iso3 = 'EST' AND (city IS NULL OR city = '')
  AND facility_name LIKE '%LABRIS%';

-- Tallinn institutions
UPDATE facility_years SET city = 'Tallinn'
WHERE country_iso3 = 'EST' AND (city IS NULL OR city = '')
  AND facility_name LIKE '%Communicable Diseases%';

UPDATE facility_years SET city = 'Tallinn'
WHERE country_iso3 = 'EST' AND (city IS NULL OR city = '')
  AND facility_name LIKE 'Microbiology Laboratory of North-Estonia%';

UPDATE facility_years SET city = 'Tallinn'
WHERE country_iso3 = 'EST' AND (city IS NULL OR city = '')
  AND facility_name LIKE 'Synlab%';

-- ── Mexico ──────────────────────────────────────────────────────────────────
-- Pattern: ALL missing Mexican rows lack city AND address.  Most major
-- research institutions are in Mexico City.

-- Mexico City institutions
UPDATE facility_years SET city = 'Mexico City'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%INER%'
       OR facility_name LIKE '%Instituto Nacional de Enfermedades Respiratorias%'
       OR facility_name LIKE '%National Institute of Respiratory Diseases%'
       OR facility_name LIKE '%Research Unit of the National Institute of Respiratory%');

UPDATE facility_years SET city = 'Mexico City'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%INCMNSZ%'
       OR facility_name LIKE '%National Institute of Medical Sciences and Nutrition%'
       OR facility_name LIKE '%Instituto Nacional de Ciencias Médicas y Nutrición%');

UPDATE facility_years SET city = 'Mexico City'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%InDRE%'
       OR facility_name LIKE '%Institute of Diagnosis and Epidemiological Reference%'
       OR facility_name LIKE '%Institute of Epidemiological Diagnosis%'
       OR facility_name LIKE '%Institute for Diagnosis and Epidemiological Reference%'
       OR facility_name LIKE '%Institute for Epidemiological Diagnosis%'
       OR facility_name LIKE '%Instituto de Diagnóstico y Referencia Epidemiológicos%'
       OR facility_name LIKE '%Dirección General de Epidemiología%'
       OR facility_name LIKE '%General Directorate of Epidemiology%');

UPDATE facility_years SET city = 'Mexico City'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%UNAM%'
       OR facility_name LIKE '%National Autonomous University of Mexico%'
       OR facility_name LIKE '%Universidad Nacional Autónoma de México%'
       OR facility_name LIKE '%Instituto de Investigaciones Biomédicas%'
       OR facility_name LIKE 'Institute of Biomedical Research'
       OR facility_name LIKE '%Biomedical Research%National Autonomous%');

UPDATE facility_years SET city = 'Mexico City'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%FMVZ%'
       OR facility_name LIKE '%Faculty of Veterinary Medicine%'
       OR facility_name LIKE '%Diagnostic Laboratory of the Department of Microbiology%');

UPDATE facility_years SET city = 'Mexico City'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%CINVESTAV%'
       OR facility_name LIKE '%Center for Research and Advanced Studies%');

UPDATE facility_years SET city = 'Mexico City'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%IPN%'
       OR facility_name LIKE '%National Polytechnic Institute%'
       OR facility_name LIKE '%National School of Biological Sciences%'
       OR facility_name LIKE '%National Laboratory of Vaccinology%'
       OR facility_name LIKE '%National Vaccinology%'
       OR facility_name LIKE '%UDIBI%'
       OR facility_name LIKE '%Unit of Development and Research in Biotherapeutics%');

UPDATE facility_years SET city = 'Mexico City'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%IMSS%'
       OR facility_name LIKE '%Mexican Institute of Social Security%');

UPDATE facility_years SET city = 'Mexico City'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%CPA%Mexico%United States Commission%'
       OR facility_name LIKE '%Comisión México Estados Unidos%'
       OR facility_name LIKE '%Mexico-United States Commission%');

-- Tecámac, Estado de México — CENASA
UPDATE facility_years SET city = 'Tecámac'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%CENASA%'
       OR facility_name LIKE '%National Center for Animal Health Diagnostic%'
       OR facility_name LIKE '%Centro Nacional de Servicios de Diagnóstico en Salud Animal%'
       OR facility_name LIKE '%National Center for Diagnostic Services in Animal Health%');

-- Guadalajara, Jalisco — CIATEJ
UPDATE facility_years SET city = 'Guadalajara'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%CIATEJ%'
       OR facility_name LIKE '%Center for Research%Technology%Design%Jalisco%'
       OR facility_name LIKE '%Research Center%Technology%Design%Jalisco%'
       OR facility_name LIKE '%Centro de Investigación en Tecnología y Diseño%Jalisco%');

-- Chihuahua — UACH
UPDATE facility_years SET city = 'Chihuahua'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%UACH%'
       OR facility_name LIKE '%Faculty of Chemical Sciences%');

-- Monterrey — UDEM
UPDATE facility_years SET city = 'Monterrey'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%UDEM%'
       OR facility_name LIKE '%University of Monterrey%');

-- State public health laboratories (LESP) — capital cities
UPDATE facility_years SET city = 'Chilpancingo'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%LESP GRO%'
       OR (facility_name LIKE '%State Public Health Laboratory%' AND facility_name LIKE '%Galo Soberón%'));

UPDATE facility_years SET city = 'Tepic'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%LESP NAY%'
       OR (facility_name LIKE '%State Public Health Laboratory%Nayarit%'));

UPDATE facility_years SET city = 'Hermosillo'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%LESP SON%'
       OR facility_name LIKE '%Sonora%');

UPDATE facility_years SET city = 'Xalapa'
WHERE country_iso3 = 'MEX' AND (city IS NULL OR city = '')
  AND (   facility_name LIKE '%LESP VER%'
       OR facility_name LIKE '%Veracruz%');

COMMIT;
