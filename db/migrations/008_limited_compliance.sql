-- Migration 008: add 'limited' as a valid form_compliance status
-- 'limited' = form is present but yields zero named facilities
-- (BSL-level-only declarations, redacted public versions, blank templates)

ALTER TABLE form_compliance
  DROP CONSTRAINT IF EXISTS form_compliance_status_check;

ALTER TABLE form_compliance
  ADD CONSTRAINT form_compliance_status_check
  CHECK (status = ANY (ARRAY[
    'substantive'::text,
    'nothing_to_declare'::text,
    'absent'::text,
    'limited'::text
  ]));
