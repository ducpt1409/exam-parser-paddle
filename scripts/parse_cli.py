"""CLI tool để test pipeline với 1 file.

TODO: Phase 1 - implement:
    python scripts/parse_cli.py input/de.pdf
    → output/{exam_id}/exam.json + images/

Cho dev/debug, không cần qua API.
"""
import click


@click.command()
@click.argument("input_path", type=click.Path(exists=True))
@click.option("--output-dir", default="./output", help="Local output dir")
@click.option("--no-upload", is_flag=True, help="Không upload MinIO, chỉ lưu local")
@click.option("--debug", is_flag=True, help="Verbose logging")
def main(input_path, output_dir, no_upload, debug):
    """Parse 1 exam file (PDF/image) → JSON + cropped images."""
    click.echo(f"📄 Input: {input_path}")
    click.echo(f"📂 Output: {output_dir}")
    click.echo(f"📤 Upload MinIO: {not no_upload}")
    # TODO: implement pipeline call
    click.echo("⚠️  Pipeline chưa implement. Xem README.md roadmap.")


if __name__ == "__main__":
    main()
