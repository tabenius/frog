-- task create used to store --priority as typed ("2"), which the scheduler
-- could not rank; store the one spelling it reads (p0-p3).
UPDATE tasks
   SET priority = 'p' || trim(priority)
 WHERE trim(priority) IN ('0', '1', '2', '3');

UPDATE tasks
   SET priority = lower(trim(priority))
 WHERE lower(trim(priority)) IN ('p0', 'p1', 'p2', 'p3')
   AND priority <> lower(trim(priority));
