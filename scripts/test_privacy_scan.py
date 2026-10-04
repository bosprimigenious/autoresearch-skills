import tempfile
import unittest
import zipfile
from pathlib import Path

from privacy_scan import format_finding, scan


class PrivacyScanTests(unittest.TestCase):
    def test_safe_public_content_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "safe.md").write_text(
                "Public example at https://github.com/example/project\n"
                "api_key=${API_KEY}\n"
                "Contact maintainer@example.com\n"
                "https://example.com/download?token=<TOKEN>\n",
                encoding="utf-8",
            )
            (root / ".env.example").write_text("TOKEN=YOUR_API_KEY\n", encoding="utf-8")
            with zipfile.ZipFile(root / "safe.pptx", "w") as archive:
                archive.writestr("docProps/core.xml", "<dc:creator>REDACTED</dc:creator>")
            self.assertEqual(scan(root), [])

    def test_private_patterns_are_classified_without_echoing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_value = "sk-" + "A" * 24
            private_url = "https://team." + "feishu" + ".cn/wiki/private"
            email = "person" + "@" + "private-company.biz"
            private_home = "/" + "Users" + "/private-name/project"
            private_ip = ".".join(("192", "168", "2", "4"))
            values = [private_value, private_url, email, private_home, private_ip]
            (root / "unsafe.txt").write_text("\n".join(values), encoding="utf-8")
            findings = scan(root)
            codes = {finding.code for finding in findings}
            self.assertEqual(codes, {"API_TOKEN", "PRIVATE_COLLAB_URL", "EMAIL", "MAC_HOME", "PRIVATE_IPV4"})
            output = "\n".join(format_finding(finding, root) for finding in findings)
            for value in values:
                self.assertNotIn(value, output)

    def test_credential_assignment_and_capability_url_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_value = "real-value-" + "7" * 20
            (root / "config.txt").write_text(
                f"client_secret={private_value}\nhttps://downloads.example.org/a?signature={private_value}\n",
                encoding="utf-8",
            )
            findings = scan(root)
            self.assertEqual({item.code for item in findings}, {"CAPABILITY_URL", "CREDENTIAL_ASSIGNMENT"})
            output = "\n".join(format_finding(item, root) for item in findings)
            self.assertNotIn(private_value, output)

    def test_zip_and_ooxml_members_are_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_value = "sk-" + "Z" * 24
            with zipfile.ZipFile(root / "submission.zip", "w") as archive:
                archive.writestr("reports/result.txt", private_value)
                archive.writestr("keys/id_ed25519", "placeholder")
                archive.comment = ("Authorization: Bearer " + "Q" * 24).encode()
            with zipfile.ZipFile(root / "report.docx", "w") as archive:
                creator = "dc:" + "creator"
                archive.writestr(
                    "docProps/core.xml",
                    f"<cp:coreProperties><{creator}>Private Person</{creator}></cp:coreProperties>",
                )
            findings = scan(root)
            codes = {item.code for item in findings}
            self.assertTrue({"API_TOKEN", "SENSITIVE_FILENAME", "DOCUMENT_AUTHOR_METADATA"}.issubset(codes))
            output = "\n".join(format_finding(item, root) for item in findings)
            self.assertNotIn(private_value, output)
            self.assertNotIn("id_ed25519", output)

    def test_transient_cache_members_in_release_archive_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root / "submission.zip", "w") as archive:
                archive.writestr("package/__pycache__/method.cpython-312.pyc", b"bytecode")
                archive.writestr("package/._manifest.json", b"metadata")
            findings = scan(root)
            transient = [item for item in findings if item.code == "TRANSIENT_ARCHIVE_ARTIFACT"]
            self.assertEqual(len(transient), 2)

    def test_capability_member_name_is_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_value = "member-secret-" + "5" * 20
            with zipfile.ZipFile(root / "bundle.zip", "w") as archive:
                archive.writestr(f"https://downloads.example.org/item?token={private_value}", "safe")
                archive.writestr(f"token={private_value}.txt", "safe")
            findings = scan(root)
            output = "\n".join(format_finding(finding, root) for finding in findings)
            self.assertIn("CAPABILITY_URL", {finding.code for finding in findings})
            self.assertIn("CREDENTIAL_ASSIGNMENT", {finding.code for finding in findings})
            self.assertNotIn(private_value, output)

    def test_nested_archive_and_binary_strings_are_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_home = "/" + "Users" + "/hidden-owner/work"
            nested_path = root / "nested.zip"
            inner = root / "inner.zip"
            with zipfile.ZipFile(inner, "w") as archive:
                archive.writestr("payload.bin", b"\x00\xffprefix:" + private_home.encode() + b"\x00")
            with zipfile.ZipFile(nested_path, "w") as archive:
                archive.write(inner, "inner.zip")
            inner.unlink()
            findings = scan(root)
            self.assertIn("MAC_HOME", {item.code for item in findings})
            output = "\n".join(format_finding(item, root) for item in findings)
            self.assertNotIn(private_home, output)

    def test_invalid_archive_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.xlsx").write_bytes(b"not a zip container")
            self.assertIn("ARCHIVE_UNREADABLE", {item.code for item in scan(root)})

    def test_archive_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            link = zipfile.ZipInfo("artifact-link")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            with zipfile.ZipFile(root / "submission.zip", "w") as archive:
                archive.writestr(link, "target")
            self.assertIn("ARCHIVE_SYMLINK", {item.code for item in scan(root)})

    def test_sensitive_disk_filename_is_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sensitive = root / "id_rsa"
            sensitive.write_text("placeholder\n", encoding="utf-8")
            findings = scan(root)
            self.assertIn("SENSITIVE_FILENAME", {item.code for item in findings})
            output = "\n".join(format_finding(item, root) for item in findings)
            self.assertNotIn(sensitive.name, output)
            self.assertIn("<redacted-", output)

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.txt"
            target.write_text("public\n", encoding="utf-8")
            (root / "alias.txt").symlink_to(target)
            self.assertIn("SYMLINK", {finding.code for finding in scan(root)})


if __name__ == "__main__":
    unittest.main()
