"""Binary attachments: pictures a document places on a page.

Kept out of the document JSON entirely. That blob is deep-copied on every op
batch, rehashed by ``content_hash`` and walked by four drift guards, so a 2MB
photo living inside it would make every keystroke slow. It lives in its own
table, content-addressed, and the document holds only an id.
"""
