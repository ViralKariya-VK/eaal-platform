# Setting up CAVY for a college

How CAVY models the way a college really runs, and how to load the students.

## The ideas

- **Programmes** are fixed and loaded, not invented: MSc Data Science, MSc Statistics and Data Science,
  MSc Applied Statistics and Analytics (2 years each) and the same three as BSc (3 years each). In the admin
  panel, **Courses & classes → Set up the NMIMS programmes** creates them (safe to press twice).
- **A student is identified by a SAP ID and an admission year.** The year of study is never typed: it is
  worked out from the admission year and the date. The academic year starts on **1 July**, so a student
  admitted in 2025 is in year 2 from 1 July 2026, and a two-year programme ends on 30 June 2027 (the student
  then shows as "Completed" and keeps their history).
- **Semester** follows the date: July to December is the odd semester, January to June the even one.
  Year 2 in October 2026 is **Sem III**; year 3 is **Sem V**.
- **Batch of** is shown as the years the group studies together: admitted 2025 on a 2-year programme is
  **2025-2027**; admitted 2024 on a 3-year programme is **2024-2027**.
- **Division and batch stay with the student** from year to year. **Batch 1** is the first half of a
  division in roll-number order, **Batch 2** the second half.
- **Sign-in** is with the SAP ID (or an email, if the person has one). A student loaded by the college gets a
  temporary password, chooses their own at first sign-in, and is never asked again for course, year, division
  or batch.

## Loading the students

Prepare an Excel (or CSV) file with a **SAP ID** column and a **Name** column, and a **Roll No** column if you
have roll numbers. Use one sheet per division and name each sheet so it says what it is:

`MSc DS Div-A (Sem-III)`, `MSc SDS Div-B (Sem-III)`, `BSc DS Div-A (Sem-V)` ...

(`DS` = Data Science, `SDS` = Statistics and Data Science, `ASA` = Applied Statistics and Analytics; the full
names work too.) The sheet name gives the programme, the division and the semester, and from the semester and
today's date CAVY works out the year of study and the admission year. Sheets without a SAP ID column (an
attendance sheet, for example) are ignored.

- **Roll numbers** are kept as given (gaps are fine). If a sheet has none, they are numbered `A001`, `A002`...
  in ascending SAP ID order.
- **Names** written in capitals are tidied (`ANUYA NALAVADE` becomes `Anuya Nalavade`).
- A **SAP ID used twice**, a missing name, or a programme that is not set up is never guessed at: the row is
  listed as "left out" and the rest is loaded.
- Importing the same file again **updates** people in place (name, roll number, division, batch) and keeps
  their passwords.

### In the admin panel

**Courses & classes → Import students.** Choose the file and press **Check the file**: you see the students per
sheet with the programme, division, semester, batch-of and the Batch 1 / Batch 2 counts, plus every row that
would be left out. Nothing is saved yet. Press **Create accounts** to load them. The temporary passwords are
shown **once**: download the CSV and hand them out.

### From the command line

```bash
python scripts/import_roster.py data/Students.xlsx --db /path/to/server.db --dry-run
python scripts/import_roster.py data/Students.xlsx --db /path/to/server.db --credentials passwords.csv
```

Keep the student list and the passwords file out of the repository: `data/` and `*temporary-passwords*.csv`
are ignored by Git.

## What is not built yet

- Subjects per semester and **teaching assignments** (professor, practical subject, division, batch, per
  academic year), and showing a student only the labs of the professor who takes their batch.
- A lab given to several batches at once with a time for each, and "duplicate this lab".
- A "start the new academic year" screen (add the new first years, retire completed students, copy last year's
  assignments).

Until then, a professor's class is still formed from the course, division and batch groups they choose, and a
student's year moves up on its own on 1 July.
