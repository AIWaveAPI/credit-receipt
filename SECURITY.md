# Security and private billing records

Credit Receipt is offline and requires no provider credentials. Do not paste account statements, task text, identifiers, API keys, or generated reports into public issues or pull requests.

Please report vulnerabilities through GitHub private vulnerability reporting when available. Use a synthetic reproducer. A useful report names the affected version, input format, unexpected behavior, and why it could expose or alter local data.

The repository's examples are synthetic. The tool does not verify that a user-supplied billing file came from its claimed provider. Output reports inherit the sensitivity of their input identifiers and should be stored privately.

`workbuddy-import` restricts its new output directory before writing usage data:
owner-only mode on POSIX, or a current-account-only inherited ACL on Windows.
`audit --output` secures its temporary report file before writing task IDs or
source refs, then atomically replaces the requested output. A permission
failure leaves any existing report unchanged. The source workbook, input files,
parent directory, copies and backups are still your responsibility. Keep
reports outside public repositories and never commit them.
