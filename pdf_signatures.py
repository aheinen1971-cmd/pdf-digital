# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
import tempfile


def sign_pdf(source, target, certificate, password):
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign import fields, signers

    signer = signers.SimpleSigner.load_pkcs12(str(certificate), passphrase=password.encode("utf-8"))
    if signer is None:
        raise ValueError("Die Zertifikatsdatei oder ihr Passwort konnte nicht gelesen werden.")
    target = Path(target)
    fd, temp = tempfile.mkstemp(dir=target.parent, suffix=".pdf")
    try:
        with open(source, "rb") as incoming, os.fdopen(fd, "wb") as outgoing:
            writer = IncrementalPdfFileWriter(incoming)
            if writer.security_handler is not None:
                raise ValueError("Bitte eine ungeschützte PDF zum Signieren auswählen.")
            existing = {name for name, _, _ in fields.enumerate_sig_fields(writer)}
            number = 1
            while f"PDFWerkbankSignatur{number}" in existing:
                number += 1
            name = f"PDFWerkbankSignatur{number}"
            signers.sign_pdf(writer, signers.PdfSignatureMetadata(field_name=name), signer=signer,
                             new_field_spec=fields.SigFieldSpec(name), output=outgoing)
        with open(temp, "rb") as signed:
            reader = PdfFileReader(signed)
            if not any(signature.field_name == name for signature in reader.embedded_signatures):
                raise ValueError("Die Signatur konnte in der erzeugten PDF nicht gefunden werden.")
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)


def verify_pdf(path, trusted_cert=None):
    from pyhanko.keys import load_cert_from_pemder
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    roots = [load_cert_from_pemder(str(trusted_cert))] if trusted_cert else None
    context = ValidationContext(trust_roots=roots, allow_fetching=False)
    results = []
    with open(path, "rb") as stream:
        reader = PdfFileReader(stream)
        for signature in reader.embedded_regular_signatures:
            try:
                status = validate_pdf_signature(signature, signer_validation_context=context)
                results.append((signature.field_name, status.intact and status.valid,
                                bool(status.trusted), bool(status.bottom_line), status.pretty_print_details()))
            except Exception as exc:
                results.append((signature.field_name, False, False, False, str(exc)))
    return results
