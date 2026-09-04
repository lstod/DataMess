---
name: classify-document
description: Identify what each file in a folder of client paperwork actually is, and where one document ends and the next begins. Use when triaging a mixed inbox of SOWs, invoices, remittances and statements before extracting anything from them, when a PDF may hold more than one document, when the same document may have been filed twice, or when something in the folder may not belong there at all.
---

# Classify a document

You are triaging a folder that a client sent, not a dataset that somebody prepared. Work out
what each file holds before anything tries to read figures out of it.

**A file is not a document.** That is the single idea this Skill exists to hold on to. One PDF
can carry an invoice with a remittance advice behind it. One document can arrive twice under
two filenames. Get this wrong and everything downstream is wrong in a way that still validates:
totals double-count, page references point at the wrong page, and the numbers look fine.

## Emit one record per document

Not one per file. A two-document PDF produces two records that share a `source_file` and differ
in `pages`. A document filed twice produces two records that share a `document_id`, one of them
carrying `duplicate_of`.

```json
{
  "source_file": "scan_20260814_113255.pdf",
  "pages": [1, 1],
  "document_type": "invoice",
  "document_id": "INV-2026-4487",
  "supersedes": null,
  "duplicate_of": null,
  "fields": {}
}
```

`pages` is inclusive and 1-based, and the second number is never smaller than the first. For a
`.docx` or an `.xlsx`, use `[1, 1]`: a Word file has no pagination until something lays it out,
and a page number you invented is a citation nobody can go and check.

## Choose exactly one of six types

| Type | What it is |
|---|---|
| `sow` | A statement of work. Scope, deliverables, a ceiling, signature blocks |
| `sow_amendment` | Varies an existing SOW. Names the SOW it amends and usually moves the ceiling |
| `invoice` | A request for payment. An invoice number, a total due, a payment date |
| `remittance` | Advice that payment **has been made**. Lists the invoices it settles |
| `statement` | A statement of account. Several invoices and a balance outstanding |
| `out_of_scope` | Real, and none of the above |

The list is closed. Nothing else is a valid answer.

**An invoice and a remittance are easy to confuse and must not be.** An invoice asks for money
and a remittance confirms money already moved. A remittance carries no total due and no payment
terms, and its table lists other documents' numbers rather than deliverables. If a document is
titled REMITTANCE and lists three invoice numbers, it is one document — not three.

## Set document_id to the natural key, read off the page

The invoice number, the SOW reference, the amendment reference, the remittance advice number,
the statement reference. This is what deduplication and reconciliation join on.

**Never the filename.** `scan_20260814_113255.pdf` is a scanner's default and says nothing about
its contents. Two files with different names routinely hold the same document.

**Never anything containing the client name.** Client names in these folders are often near
twins — two entities sharing every word but the first are common — so a key built from a name
matches the wrong client sooner or later, and silently.

`out_of_scope` documents get `null`.

## Look for four things that are easy to miss

**A second document behind the first.** Scan every page, not just page 1. A remittance appended
to an invoice is a different document that happens to share a file, and the giveaway is a
second letterhead or title mid-file. Give each its own record and its own page range.

**An amendment and the SOW it replaces, both in the folder.** Set `supersedes` on the amendment
to the `document_id` of the SOW it amends. The original will usually be the one marked FINAL and
signed; nothing in either filename says which is current. Only a `sow_amendment` may supersede
anything.

**The same document under two names.** `INV-0412.pdf` and `INV-0412 (1).pdf`, or the same
invoice once as a PDF and once as a scan. Compare the `document_id`, never the filename and
never the file size. Set `duplicate_of` on the later of the two to the shared `document_id`, and
leave the first alone.

**Something that does not belong.** A lunch receipt, a parking ticket, a personal expense claim,
a supplier's marketing email. Mark it `out_of_scope`, give it no `document_id`, and **leave
`fields` empty.** Do not force-fit it to the schema.

The hardest of these is the expense claim: it has a reference, a date, a client name and a
total, and every rule of the form *reference plus total means invoice* puts it in the accounts
receivable. What makes it out of scope is who owes whom. An invoice is the firm asking a client
for money. An expense claim is a person asking the firm.

## Do not

- Do not emit one record per file when a file holds two documents.
- Do not drop the second document because the first one parsed cleanly.
- Do not deduplicate on the filename, the file size or the byte length.
- Do not guess a type from the filename. `FINAL v3 (signed).docx` is a SOW about half the time.
- Do not put a value in `fields` here. Classification decides *what* the document is; the
  extraction step reads what it says.
- Do not skip a file because it has no text layer. About a third of these are scans and return
  nothing to a text extractor. **An empty text extraction is not an empty document** — look at
  the page.

## Check before you finish

- Every file in the folder appears in at least one record.
- The number of records is greater than or equal to the number of files, and you can say which
  files produced more than one and why.
- Every `document_id` outside `out_of_scope` is non-null, and no two documents that are
  genuinely different share one.
- Every `supersedes` and `duplicate_of` names a `document_id` that exists in your output.
- Every `out_of_scope` record has a null `document_id` and an empty `fields`.
