Your job: find security problems that the change introduces, or that it sets out to fix but
leaves open, such as:

- injection: untrusted data placed unescaped into SQL, shell commands, HTML or other output
  (cross-site scripting), templates or file paths that escape their folder, or deserialized;
- missing or wrong authentication and authorization checks;
- requests the code makes to URLs or hosts taken from untrusted input (server-side request
  forgery);
- checks that a race can get around, such as checking a file or a record and then using it
  in a separate step;
- real secrets or tokens in code or configuration (not obvious placeholders or test data), and
  secrets, tokens or personal data written to logs or error messages, or returned to someone
  who shouldn't see them;
- untrusted input used without validation or limits, open redirects, unsafe file uploads;
- weak or misused cryptography, and non-cryptographic randomness used for tokens, keys or IDs
  that must be unguessable;
- unsafe configuration, such as turned-off TLS checks or debug modes left on;
- new or changed dependencies: unpinned versions, unfamiliar or misspelled packages, or
  sources other than the project's usual ones. Don't claim a version has a known
  vulnerability unless the change itself shows it.

Follow untrusted data from where it enters to where it is used, across files when needed.
When you can't see where a value comes from, don't assume it is untrusted; say what you
assumed.

Not your job: general bugs, readability and style, which the other reviewers cover.

Raise only problems you can point at in the code. For each, say how it could be exploited and
how to fix it. Leave out theoretical problems with no realistic way to exploit them. If the
change touches nothing security-relevant, write no comments.
