from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pwc_cli.cli import build_parser, main  # noqa: E402
from pwc_cli.skills import build_skill_md  # noqa: E402
from pwc_cli.transport import Client, Response  # noqa: E402

INSTALLER_SPEC = importlib.util.spec_from_file_location(
    "pwc_cli_installer", ROOT / "install.py"
)
assert INSTALLER_SPEC and INSTALLER_SPEC.loader
installer = importlib.util.module_from_spec(INSTALLER_SPEC)
INSTALLER_SPEC.loader.exec_module(installer)


def test_complete_parser_omits_catalog_mutation_and_auth_commands():
    help_text = build_parser().format_help()
    assert "{search,paper,task,method,conference,benchmark,skills,version}" in help_text
    for command in ("auth", "add-external", "cron", "embedding", "github-issue"):
        assert command not in help_text
    assert build_parser().parse_args(["paper", "lineage", "list", "2501.1"]).paper
    assert (
        build_parser().parse_args(["task", "--name", "scene-text-recognition"]).name
        == "scene-text-recognition"
    )
    assert build_parser().parse_args(["task", "list", "--area", "Vision"]).area
    assert build_parser().parse_args(["task", "list", "--group-by-area"]).group_by_area
    assert build_parser().parse_args(["method", "list", "--area", "Audio"]).area
    assert build_parser().parse_args(["conference", "list", "--year", "2025"]).year


def test_generated_skill_matches_installed_cli_version_and_commands():
    skill = build_skill_md()

    assert "name: pwc-cli" in skill
    assert "Generated with `pwc v0.1.8`" in skill
    assert "`pwc search QUERY" in skill
    assert "`pwc benchmark --name NAME" in skill
    assert "`pwc skills add" in skill


def test_skills_add_installs_project_skill(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["skills", "add"]) == 0

    skill_file = tmp_path / ".agents" / "skills" / "pwc-cli" / "SKILL.md"
    assert skill_file.read_text() == build_skill_md()
    assert str(skill_file.parent) in capsys.readouterr().out


def test_skills_add_global_and_claude_uses_user_directories(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert main(["skills", "add", "--global", "--claude"]) == 0

    central = tmp_path / ".agents" / "skills" / "pwc-cli"
    claude = tmp_path / ".claude" / "skills" / "pwc-cli"
    assert (central / "SKILL.md").is_file()
    assert claude.is_symlink()
    assert claude.resolve() == central
    output = capsys.readouterr().out
    assert str(central) in output
    assert str(claude) in output


def test_skills_add_protects_existing_install_and_force_replaces_it(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / ".agents" / "skills" / "pwc-cli"
    existing.mkdir(parents=True)
    (existing / "old.txt").write_text("keep unless forced")

    assert main(["skills", "add"]) == 2
    assert (existing / "old.txt").is_file()
    assert "rerun with --force" in capsys.readouterr().err

    assert main(["skills", "add", "--force"]) == 0
    assert not (existing / "old.txt").exists()
    assert (existing / "SKILL.md").is_file()


def test_skills_add_supports_custom_harness_directory_and_rejects_mixed_scope(
    tmp_path, capsys
):
    custom = tmp_path / "agent-skills"

    assert main(["skills", "add", "--dest", str(custom)]) == 0
    assert (custom / "pwc-cli" / "SKILL.md").is_file()

    assert main(["skills", "add", "--dest", str(custom), "--global"]) == 2
    assert "--dest cannot be combined" in capsys.readouterr().err


def test_installer_downloads_from_this_repository_release_origin():
    source = (ROOT / "install.py").read_text()
    assert "github.com/huggingface/pwc-cli/releases/download" in source
    assert "github.com/paperswithcode/paperswithcode.co" not in source


def test_installer_version_pin_is_optional_and_explicit_pin_skips_lookup(monkeypatch):
    assert installer.parse_args([]).version is None
    monkeypatch.setattr(
        installer, "_open", lambda _url: (_ for _ in ()).throw(AssertionError())
    )
    assert installer.resolve_version("1.2.3", "unused") == "1.2.3"


def test_installer_resolves_valid_latest_release_tag(monkeypatch):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        installer,
        "_open",
        lambda _url: Response(b'{"tag_name": "pwc-cli-1.2.3"}'),
    )
    assert installer.resolve_version(None, "https://example.test/latest") == "1.2.3"


def test_installer_release_lookup_network_error_is_one_line_exit_3(monkeypatch, capsys):
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("offline\nnow")
        ),
    )
    assert installer.main([]) == 3
    assert capsys.readouterr().err == "pwc installer: network error: offline now\n"


def test_installer_artifact_and_checksum_network_errors_exit_3(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(installer.platform, "system", lambda: "Linux")
    monkeypatch.setattr(installer.platform, "machine", lambda: "x86_64")

    monkeypatch.setattr(
        installer,
        "_download",
        lambda *_args: (_ for _ in ()).throw(installer.NetworkError("artifact")),
    )
    assert installer.main(["--version", "1.2.3", "--prefix", str(tmp_path)]) == 3
    assert capsys.readouterr().err == "pwc installer: network error: artifact\n"

    monkeypatch.setattr(
        installer, "_download", lambda _url, destination: destination.write_bytes(b"x")
    )
    monkeypatch.setattr(
        installer,
        "_read_checksum",
        lambda *_args: (_ for _ in ()).throw(installer.NetworkError("checksum")),
    )
    assert installer.main(["--version", "1.2.3", "--prefix", str(tmp_path)]) == 3
    assert capsys.readouterr().err == "pwc installer: network error: checksum\n"


def test_installer_reports_path_and_persistent_instruction(tmp_path, capsys):
    bin_directory = tmp_path / ".local" / "bin"
    installer.report_path(bin_directory, f"/usr/bin:{bin_directory}")
    captured = capsys.readouterr()
    assert captured.out == f"{bin_directory} is on PATH\n"
    assert captured.err == ""

    installer.report_path(bin_directory, "/usr/bin")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"warning: {bin_directory} is not on PATH\n"
        f'Add this to ~/.profile: export PATH={bin_directory}:"$PATH"\n'
    )


def test_installer_default_path_instruction_uses_expandable_home(capsys):
    installer.report_path(Path.home() / ".local" / "bin", "/usr/bin")
    assert capsys.readouterr().err == (
        "warning: ~/.local/bin is not on PATH\n"
        'Add this to ~/.profile: export PATH="$HOME/.local/bin:$PATH"\n'
    )


def test_installer_replaces_binary_atomically(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"new executable")
    destination = tmp_path / "bin" / "pwc"
    destination.parent.mkdir()
    destination.write_bytes(b"old executable")

    installer._install_atomically(artifact, destination)

    assert destination.read_bytes() == b"new executable"
    assert destination.stat().st_mode & 0o111
    assert list(destination.parent.iterdir()) == [destination]


def test_benchmark_limit_alias_preserves_page_size_destination():
    parser = build_parser()
    assert parser.parse_args(["benchmark", "list", "--limit", "7"]).page_size == 7
    assert parser.parse_args(["benchmark", "list", "--page-size", "8"]).page_size == 8


def test_benchmark_detail_parser_matches_requested_command_shape():
    args = build_parser().parse_args(
        [
            "benchmark",
            "--name",
            "SWE-Bench Pro",
            "--limit",
            "5",
            "--require-metrics",
            "Resolved,Latency",
            "--min",
            "Resolved=50",
            "--max",
            "Latency=2.5",
            "--sort",
            "Resolved:desc",
            "--pareto",
            "Resolved:higher,Latency:lower",
        ]
    )

    assert args.name == "SWE-Bench Pro"
    assert args.limit == 5
    assert args.require_metrics == ("Resolved", "Latency")
    assert args.minimum_metrics == [("Resolved", 50)]
    assert args.maximum_metrics == [("Latency", 2.5)]
    assert args.sort_metric == ("Resolved", "desc")
    assert args.pareto == (("Resolved", "higher"), ("Latency", "lower"))


def test_task_benchmark_list_uses_frontend_trending_order(monkeypatch):
    calls = []
    payload = {
        "task_id": "18",
        "task_slug": "ocr",
        "recent_days": 90,
        "results": [
            {
                "id": "2",
                "slug": "recent-ocr",
                "name": "Recent OCR",
                "recent_paper_count": 4,
                "all_time_paper_count": 5,
                "trend_score": 1.3333,
                "best_model_name": "Reader 2",
                "best_paper_title": "Recent OCR Paper",
                "best_code_url": "https://github.com/example/reader-2",
            },
            {
                "id": "1",
                "slug": "classic-ocr",
                "name": "Classic OCR",
                "recent_paper_count": 1,
                "all_time_paper_count": 20,
                "trend_score": 0.3333,
                "best_model_name": "Reader 1",
                "best_paper_title": "Classic OCR Paper",
                "best_code_url": None,
            },
        ],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params):
            calls.append((path, params))
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["benchmark", "list", "--task", "OCR"]) == 0

    assert calls[0][0] == "tasks/OCR/trending-benchmarks"
    assert calls[0][1]["min_recent_papers"] == 0
    assert output.getvalue().splitlines() == [
        "id\tslug\tname\trecent_papers\ttrend_score\tbest_model\tpaper_title\tcode",
        (
            "2\trecent-ocr\tRecent OCR\t4\t1.3333\tReader 2\tRecent OCR Paper"
            "\thttps://github.com/example/reader-2"
        ),
        "1\tclassic-ocr\tClassic OCR\t1\t0.3333\tReader 1\tClassic OCR Paper\t",
    ]


def test_task_filter_takes_precedence_over_grouped_benchmark_display(
    monkeypatch, capsys
):
    payload = {
        "count": 1,
        "results": [
            {
                "id": "2",
                "slug": "recent-ocr",
                "name": "Recent OCR",
                "recent_paper_count": 4,
                "trend_score": 1.3333,
                "best_model_name": "Reader 2",
                "best_paper_title": "Recent OCR Paper",
            }
        ],
    }
    calls = []

    class Client:
        def __init__(self):
            pass

        def get(self, path, params):
            calls.append((path, params))
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    assert main(["benchmark", "list", "--task", "OCR", "--group-by-area"]) == 0

    captured = capsys.readouterr()
    assert calls[0][0] == "tasks/OCR/trending-benchmarks"
    assert captured.out.startswith("id\tslug\tname\trecent_papers")
    assert captured.err == (
        "# filters select task-scoped flat output; --group-by-area ignored\n"
    )


def test_benchmark_list_groups_top_benchmarks_as_markdown(monkeypatch):
    calls = []
    payload = {
        "results": [
            {
                "id": "1",
                "name": "Vision",
                "tasks": [
                    {
                        "id": "10",
                        "name": "Image Classification",
                        "slug": "image-classification",
                        "paper_count": 100,
                        "benchmarks": [
                            {
                                "id": "20",
                                "name": "ImageNet [1K]",
                                "slug": "imagenet",
                                "full_name": "ImageNet Large Scale Visual Recognition",
                                "evaluation_count": 1234,
                                "featured_rank": 1,
                            }
                        ],
                    }
                ],
            }
        ]
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params):
            calls.append((path, params))
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert (
            main(
                [
                    "benchmark",
                    "list",
                    "--group-by-area",
                    "--area",
                    "Vision",
                    "--benchmarks-per-task",
                    "5",
                ]
            )
            == 0
        )

    assert calls == [
        (
            "areas/with-task-benchmarks/",
            {"benchmarks_per_task": 5, "min_eval_count": 2},
        )
    ]
    assert output.getvalue() == (
        "# Area: Vision\n"
        "\n"
        "Inspect a benchmark with `pwc benchmark --name SLUG`.\n"
        "\n"
        "## Image Classification (`image-classification`)\n"
        "\n"
        "- ImageNet [1K] — full name: ImageNet Large Scale Visual Recognition; "
        "slug: `imagenet`; ID: `20`; 1,234 evaluations\n"
    )


def test_task_list_resolves_area_name_and_renders_compact_output(monkeypatch):
    calls = []
    areas = {
        "count": 2,
        "results": [{"id": "1", "name": "Vision"}, {"id": "6", "name": "Audio"}],
    }
    tasks = {
        "count": 1,
        "results": [
            {
                "id": "10",
                "slug": "image-classification",
                "name": "Image Classification",
                "area_id": "1",
                "level": 0,
                "paper_count": 123,
                "benchmark_count": 4,
                "evaluation_count": 567,
            }
        ],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            calls.append((path, params))
            payload = areas if path == "areas/" else tasks
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert (
            main(
                [
                    "task",
                    "list",
                    "--area",
                    "vision",
                    "--page-size",
                    "1",
                    "--order-by",
                    "paper_count",
                    "--order-dir",
                    "desc",
                ]
            )
            == 0
        )

    assert calls == [
        ("areas/", {"page": 1, "page_size": 500, "ordering": "name"}),
        (
            "tasks/",
            {
                "page": 1,
                "page_size": 1,
                "area_id": "1",
                "level": None,
                "visible_only": False,
                "ordering": "-paper_count",
            },
        ),
    ]
    assert output.getvalue() == (
        "id\tslug\tname\tarea\tlevel\tpapers\tbenchmarks\tevals\n"
        "10\timage-classification\tImage Classification\tVision\t0\t123\t4\t567\n"
    )


def test_task_list_groups_complete_taxonomy_in_interactive_terminal(monkeypatch):
    payload = {
        "results": [
            {
                "id": "6",
                "name": "Audio",
                "tasks": [
                    {
                        "id": "259",
                        "slug": "audio-classification",
                        "name": "Audio Classification",
                        "paper_count": 63,
                        "benchmark_count": 1,
                        "evaluation_count": 8,
                    }
                ],
            },
            {
                "id": "1",
                "name": "Vision",
                "tasks": [
                    {
                        "id": "13",
                        "slug": "3d-generation",
                        "name": "3D generation",
                        "paper_count": 2389,
                        "benchmark_count": 12,
                        "evaluation_count": 345,
                    },
                    {
                        "id": "1",
                        "slug": "image-classification",
                        "name": "Image Classification",
                        "paper_count": 1,
                        "benchmark_count": 1,
                        "evaluation_count": 1,
                    },
                ],
            },
        ]
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            assert (path, params) == ("areas/with-tasks/", None)
            return Response(json.dumps(payload).encode(), {})

    class TerminalOutput(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = TerminalOutput()
    with redirect_stdout(output):
        assert main(["task", "list"]) == 0

    assert output.getvalue() == (
        "# Area: Audio\n"
        "- **Audio Classification** (`audio-classification`) — "
        "63 papers · 1 benchmark · 8 evals\n"
        "\n"
        "# Area: Vision\n"
        "- **3D generation** (`3d-generation`) — "
        "2,389 papers · 12 benchmarks · 345 evals\n"
        "- **Image Classification** (`image-classification`) — "
        "1 paper · 1 benchmark · 1 eval\n"
    )
    assert "(`13`)" not in output.getvalue()


def test_task_list_can_force_grouped_area_output_when_captured(monkeypatch):
    payload = {
        "results": [
            {"id": "6", "name": "Audio", "tasks": []},
            {
                "id": "1",
                "name": "Vision",
                "tasks": [
                    {
                        "id": "13",
                        "slug": "3d-generation",
                        "name": "3D generation",
                        "paper_count": 2389,
                        "benchmark_count": 12,
                        "evaluation_count": 345,
                    }
                ],
            },
        ]
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            assert (path, params) == ("areas/with-tasks/", None)
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["task", "list", "--group-by-area", "--area", "vision"]) == 0

    assert output.getvalue() == (
        "# Area: Vision\n- **3D generation** (`3d-generation`) — "
        "2,389 papers · 12 benchmarks · 345 evals\n"
    )


def test_task_detail_renders_task_page_sections(monkeypatch):
    calls = []
    search = {
        "count": 1,
        "results": [
            {
                "id": "17",
                "slug": "scene-text-recognition",
                "name": "Scene Text Recognition",
                "area_id": "1",
                "level": 1,
                "paper_count": 204,
                "benchmark_count": 21,
                "evaluation_count": 401,
            }
        ],
    }
    page = {
        "task": {
            "id": "17",
            "slug": "scene-text-recognition",
            "name": "Scene Text Recognition",
            "description": "Recognizing text in natural images.",
            "research_trends": ["Vision-language pretraining"],
            "area_id": "1",
            "level": 1,
            "paper_count": 204,
        },
        "parents": [{"id": "2", "slug": "ocr", "name": "OCR"}],
        "sisters": [{"id": "18", "slug": "text-detection", "name": "Text Detection"}],
        "recommended_frameworks": [
            {
                "id": "44",
                "url": "https://github.com/huggingface/transformers",
                "owner": "huggingface",
                "name": "transformers",
                "num_stars": 150000,
            }
        ],
        "children": [{"id": "19", "slug": "handwritten-text", "name": "Handwriting"}],
        "benchmarks": [
            {
                "id": "30",
                "slug": "iiit5k",
                "name": "IIIT5K",
                "paper_count": 12,
                "best_model_name": "Model A",
            }
        ],
        "subtask_benchmarks": [
            {
                "subtask_id": "19",
                "benchmarks": [{"id": "31", "slug": "iam", "name": "IAM"}],
            }
        ],
    }
    papers = {
        "count": 204,
        "results": [
            {
                "id": "100",
                "arxiv_id": "2501.00001",
                "title": "A Better Text Recognizer",
                "published": "2025-01-01",
                "citation_count": 10,
                "methods": [{"id": "7", "slug": "transformer", "name": "Transformer"}],
            }
        ],
    }
    areas = {"count": 1, "results": [{"id": "1", "name": "Vision"}]}

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            calls.append((path, params))
            payloads = {
                "tasks/": search,
                "tasks/17/page": page,
                "tasks/17/papers": papers,
                "areas/": areas,
            }
            return Response(json.dumps(payloads[path]).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["task", "--name", "scene-text-recognition"]) == 0

    rendered = output.getvalue()
    assert "# Scene Text Recognition" in rendered
    assert (
        "Area: Vision · Level: 1 · 204 papers · 21 benchmarks · 401 evaluations"
        in rendered
    )
    assert "## Recommended frameworks" in rendered
    assert (
        "[huggingface/transformers](https://github.com/huggingface/transformers)"
        in rendered
    )
    assert "## Sister tasks" in rendered
    assert "## Common methods (from 1 sampled papers)" in rendered
    assert "## Benchmarks by subtask" in rendered
    assert "## Trending papers (top 1 of 204)" in rendered
    assert calls == [
        ("tasks/", {"q": "scene-text-recognition", "page": 1, "page_size": 100}),
        ("tasks/17/page", None),
        (
            "tasks/17/papers",
            {
                "page": 1,
                "page_size": 100,
                "order_by": "trending",
                "order_dir": "desc",
                "include_resources": True,
            },
        ),
        ("areas/", {"page": 1, "page_size": 500, "ordering": "name"}),
    ]


def test_task_detail_json_has_stable_composed_payload(monkeypatch, capsys):
    payloads = {
        "tasks/": {
            "count": 1,
            "results": [
                {
                    "id": "17",
                    "slug": "scene-text-recognition",
                    "name": "Scene Text Recognition",
                    "area_id": "1",
                }
            ],
        },
        "tasks/17/page": {
            "task": {"id": "17", "name": "Scene Text Recognition"},
            "recommended_frameworks": [],
        },
        "tasks/17/papers": {"count": 0, "results": []},
        "areas/": {"count": 1, "results": [{"id": "1", "name": "Vision"}]},
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            return Response(json.dumps(payloads[path]).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    assert main(["task", "--name", "Scene Text Recognition", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "v1"
    assert result["data"]["task"]["id"] == "17"
    assert result["data"]["area"]["name"] == "Vision"
    assert result["data"]["paper_sample_size"] == 0


def test_method_list_accepts_area_id_and_year_filter(monkeypatch):
    calls = []
    areas = {
        "count": 1,
        "results": [{"id": "6", "name": "Audio"}],
    }
    methods = {
        "count": 1,
        "results": [
            {
                "id": "20",
                "slug": "wav2vec-2",
                "name": "wav2vec 2.0",
                "full_name": "wav2vec 2.0",
                "area_id": "6",
                "introduced_year": 2020,
                "paper_count": 45,
            }
        ],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            calls.append((path, params))
            payload = areas if path == "areas/" else methods
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert (
            main(
                [
                    "method",
                    "list",
                    "--area",
                    "6",
                    "--introduced-year",
                    "2020",
                ]
            )
            == 0
        )

    assert calls[1] == (
        "methods/",
        {
            "page": 1,
            "page_size": 50,
            "area_id": "6",
            "introduced_year": 2020,
            "ordering": "name",
        },
    )
    assert output.getvalue() == (
        "id\tslug\tname\tfull_name\tarea\tintroduced\tpapers\n"
        "20\twav2vec-2\twav2vec 2.0\twav2vec 2.0\tAudio\t2020\t45\n"
    )


def test_conference_list_filters_complete_response_by_year(monkeypatch):
    payload = {
        "count": 2,
        "results": [
            {
                "slug": "cvpr-2025",
                "name": "CVPR 2025",
                "year": 2025,
                "paper_count": 1834,
                "location": "Nashville, USA",
            },
            {
                "slug": "cvpr-2026",
                "name": "CVPR 2026",
                "year": 2026,
                "paper_count": 2200,
                "location": "Denver, USA",
            },
        ],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            assert (path, params) == ("conferences/", None)
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["conference", "list", "--year", "2025"]) == 0

    assert output.getvalue() == (
        "slug\tname\tyear\tpapers\tlocation\n"
        "cvpr-2025\tCVPR 2025\t2025\t1834\tNashville, USA\n"
    )


def test_conference_list_aligns_columns_in_interactive_terminal(monkeypatch):
    payload = {
        "count": 2,
        "results": [
            {
                "slug": "cvpr-2025",
                "name": "CVPR 2025",
                "year": 2025,
                "paper_count": 1834,
                "location": "Nashville, USA",
            },
            {
                "slug": "neurips-2025",
                "name": "NeurIPS 2025",
                "year": 2025,
                "paper_count": 2763,
                "location": "San Diego, USA",
            },
        ],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            assert (path, params) == ("conferences/", None)
            return Response(json.dumps(payload).encode(), {})

    class TerminalOutput(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = TerminalOutput()
    with redirect_stdout(output):
        assert main(["conference", "list"]) == 0

    assert output.getvalue() == (
        "slug          name          year  papers  location\n"
        "cvpr-2025     CVPR 2025     2025    1834  Nashville, USA\n"
        "neurips-2025  NeurIPS 2025  2025    2763  San Diego, USA\n"
    )


def test_unknown_area_lists_valid_choices(monkeypatch):
    areas = {
        "count": 2,
        "results": [{"id": "1", "name": "Vision"}, {"id": "6", "name": "Audio"}],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            assert path == "areas/"
            return Response(json.dumps(areas).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    errors = io.StringIO()
    with redirect_stderr(errors):
        assert main(["task", "list", "--area", "Robotics"]) == 4
    assert errors.getvalue() == (
        "pwc: Area not found: Robotics; available areas: Audio, Vision\n"
    )


def test_benchmark_detail_renders_merged_markdown_leaderboard(monkeypatch):
    calls = []
    benchmark_payload = {
        "count": 1,
        "results": [
            {
                "id": "42",
                "name": "SWE-Bench Pro",
                "slug": "swe-bench-pro",
                "description": "A coding-agent benchmark.",
            }
        ],
    }
    evaluations_payload = {
        "count": 2,
        "results": [
            {
                "id": "1",
                "paper_id": "9",
                "task_id": "3",
                "dataset_id": "42",
                "model_name": "Agent | One",
                "metrics": {"Resolved": 55.5},
                "best_metric": "Resolved",
                "best_rank": 1,
                "paper_title": "An Agent Paper",
                "paper_arxiv_id": "2601.12345",
                "paper_published_date": "2026-01-20",
                "task_name": "Coding Agents",
                "is_open": False,
            },
            {
                "id": "2",
                "paper_id": "9",
                "task_id": "3",
                "dataset_id": "42",
                "model_name": "Agent | One",
                "metrics": {"Pass@1": 50},
                "best_metric": "Pass@1",
                "best_rank": 2,
                "paper_title": "An Agent Paper",
                "paper_arxiv_id": "2601.12345",
                "paper_published_date": "2026-01-20",
                "task_name": "Coding Agents",
                "is_open": False,
            },
        ],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params):
            calls.append((path, params))
            payload = benchmark_payload if path == "datasets/" else evaluations_payload
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["benchmark", "--name", "SWE-Bench Pro"]) == 0

    rendered = output.getvalue()
    assert "# SWE-Bench Pro" in rendered
    assert "Agent \\| One" in rendered
    assert "Resolved: 55.5, Pass@1: 50" in rendered
    assert "[An Agent Paper](https://paperswithcode.co/paper/2601.12345)" in rendered
    assert "2026-01-20" in rendered
    assert calls[1] == (
        "datasets/42/evaluations/",
        {
            "page": 1,
            "page_size": 100,
            "ordering": "best_rank",
            "is_open": None,
        },
    )


def test_benchmark_detail_renders_aligned_table_in_terminal(monkeypatch):
    benchmark_payload = {
        "count": 1,
        "results": [
            {
                "id": "42",
                "name": "SWE-Bench Pro",
                "slug": "swe-bench-pro",
                "description": "A coding-agent benchmark.",
            }
        ],
    }
    evaluations_payload = {
        "count": 1,
        "results": [
            {
                "id": "1",
                "paper_id": "9",
                "task_id": "3",
                "dataset_id": "42",
                "model_name": "Agent One",
                "metrics": {"Resolved": 55.5},
                "best_metric": "Resolved",
                "best_rank": 1,
                "paper_title": "An Agent Paper",
                "paper_arxiv_id": "2601.12345",
                "paper_published_date": "2026-01-20",
                "task_name": "Coding Agents",
                "is_open": False,
            }
        ],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params):
            payload = benchmark_payload if path == "datasets/" else evaluations_payload
            return Response(json.dumps(payload).encode(), {})

    class TerminalBuffer(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = TerminalBuffer()
    with redirect_stdout(output):
        assert main(["benchmark", "--name", "SWE-Bench Pro"]) == 0

    rendered = output.getvalue()
    assert rendered.startswith("SWE-Bench Pro\n\nA coding-agent benchmark.\n")
    assert (
        "View benchmark: https://paperswithcode.co/benchmark/swe-bench-pro" in rendered
    )
    assert "Rank  Model      Scores          Task" in rendered
    assert "   1  Agent One  Resolved: 55.5  Coding Agents" in rendered
    assert "An Agent Paper [2601.12345]" in rendered
    assert "| ---:" not in rendered


def test_benchmark_detail_filters_sorts_and_computes_pareto_frontier(
    monkeypatch, capsys
):
    benchmark_payload = {
        "count": 1,
        "results": [{"id": "6", "name": "COCO", "slug": "coco"}],
    }
    evaluation_pages = {
        1: {
            "count": 3,
            "results": [
                {
                    "id": "1",
                    "paper_id": "1",
                    "task_id": "3",
                    "dataset_id": "6",
                    "model_name": "Accurate",
                    "metrics": {"mAP": 60, "FPS": 30},
                    "best_rank": 1,
                },
                {
                    "id": "2",
                    "paper_id": "2",
                    "task_id": "3",
                    "dataset_id": "6",
                    "model_name": "Fast",
                    "metrics": {"mAP": "58", "FPS": "100"},
                    "best_rank": 2,
                },
            ],
        },
        2: {
            "count": 3,
            "results": [
                {
                    "id": "3",
                    "paper_id": "3",
                    "task_id": "3",
                    "dataset_id": "6",
                    "model_name": "Dominated",
                    "metrics": {"mAP": 57, "FPS": 50},
                    "best_rank": 3,
                }
            ],
        },
    }
    calls = []

    class Client:
        def __init__(self):
            pass

        def get(self, path, params):
            calls.append((path, params))
            if path == "datasets/":
                return Response(json.dumps(benchmark_payload).encode(), {})
            return Response(json.dumps(evaluation_pages[params["page"]]).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    assert (
        main(
            [
                "benchmark",
                "--name",
                "COCO",
                "--pareto",
                "mAP:higher,FPS:higher",
                "--sort",
                "FPS:desc",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    assert (
        "Showing 2 of 2 matching merged rows from 3 stored evaluation rows."
        in captured.out
    )
    assert captured.out.index("Fast") < captured.out.index("Accurate")
    assert "Dominated" not in captured.out
    assert [params["page"] for path, params in calls if path != "datasets/"] == [1, 2]


def test_benchmark_detail_metric_thresholds_and_unknown_metric(monkeypatch, capsys):
    benchmark_payload = {
        "count": 1,
        "results": [{"id": "6", "name": "COCO", "slug": "coco"}],
    }
    evaluations_payload = {
        "count": 2,
        "results": [
            {
                "paper_id": "1",
                "task_id": "3",
                "dataset_id": "6",
                "model_name": "Fast",
                "metrics": {"mAP": 58, "FPS": 100},
            },
            {
                "paper_id": "2",
                "task_id": "3",
                "dataset_id": "6",
                "model_name": "Slow",
                "metrics": {"mAP": 62, "FPS": 20},
            },
        ],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, _params):
            payload = benchmark_payload if path == "datasets/" else evaluations_payload
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    assert (
        main(
            [
                "benchmark",
                "--name",
                "COCO",
                "--require-metrics",
                "mAP,FPS",
                "--min",
                "FPS=60",
                "--max",
                "mAP=60",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "Fast" in captured.out
    assert "Slow" not in captured.out

    assert main(["benchmark", "--name", "COCO", "--sort", "Latency"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unknown metric(s): latency; available metrics: FPS, mAP" in captured.err


def test_top_level_version_is_offline_and_stable():
    output = io.StringIO()
    with redirect_stdout(output):
        try:
            build_parser().parse_args(["--version"])
        except SystemExit as error:
            assert error.code == 0
    assert output.getvalue() == "pwc 0.1.8\tapi v1\n"


def test_search_default_output_is_compact_deterministic_tsv(monkeypatch):
    payload = {
        "count": 1,
        "results": [
            {
                "arxiv_id": "2501.01234",
                "title": "Line one\nline two",
                "published": "2025-01-03",
                "citation_count": 42,
            }
        ],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, _path, _params):
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["search", "vision"]) == 0
    assert output.getvalue() == (
        "id\ttitle\tyear\tcitations\n2501.01234\tLine one\\nline two\t2025\t42\n"
    )


def test_all_paper_commands_resolve_exact_titles(monkeypatch):
    title = "Transformers Are RNNs"
    resolved = "2006.16236"
    search_payload = {
        "count": 1,
        "results": [{"arxiv_id": resolved, "title": title}],
    }
    calls = []

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            calls.append((path, params))
            if path == "papers/search":
                return Response(json.dumps(search_payload).encode(), {})
            if path == f"papers/{resolved}":
                return Response(
                    json.dumps({"arxiv_id": resolved, "title": title}).encode(), {}
                )
            if path == f"research/papers/{resolved}/read":
                return Response(b"# Paper", {})
            if path == f"papers/{resolved}/related":
                return Response(b'{"count":0,"results":[]}', {})
            if path == f"research/papers/{resolved}/lineage":
                return Response(
                    json.dumps(
                        {
                            "paper": {"reference": resolved, "title": title},
                            "predecessors": [],
                            "successors": [],
                        }
                    ).encode(),
                    {},
                )
            raise AssertionError(path)

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    commands = (
        (
            ["paper", "info", title.lower()],
            (f"papers/{resolved}", {"include_resources": False}),
        ),
        (
            ["paper", "read", title.lower()],
            (f"research/papers/{resolved}/read", None),
        ),
        (
            ["paper", "related", title.lower()],
            (f"papers/{resolved}/related", {"limit": 4}),
        ),
        (
            ["paper", "lineage", "list", title.lower()],
            (f"research/papers/{resolved}/lineage", None),
        ),
    )

    for argv, expected_request in commands:
        calls.clear()
        with redirect_stdout(io.StringIO()):
            assert main(argv) == 0
        assert calls == [
            (
                "papers/search",
                {
                    "q": title.lower(),
                    "page": 1,
                    "page_size": 20,
                    "mode": "keyword",
                },
            ),
            expected_request,
        ]


def test_ambiguous_paper_title_is_rejected_with_matches(monkeypatch):
    title = "A Shared Title"
    payload = {
        "count": 2,
        "results": [
            {"arxiv_id": "2501.00001", "title": title},
            {"arxiv_id": "2501.00002", "title": title},
        ],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            assert path == "papers/search"
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    errors = io.StringIO()
    with redirect_stderr(errors):
        assert main(["paper", "info", title]) == 4
    assert errors.getvalue() == (
        "pwc: Paper title is ambiguous: A Shared Title; "
        "A Shared Title (2501.00001); A Shared Title (2501.00002)\n"
    )


def test_paper_info_renders_metadata_and_abstract_sections(monkeypatch):
    payload = {
        "arxiv_id": "2501.01234",
        "title": "A Paper",
        "abstract": "First line.\nSecond line.",
        "published": "2025-01-03",
        "authors": ["Ada Researcher", "Grace Scientist"],
        "citation_count": 42,
        "url_abs": "https://paperswithcode.co/paper/2501.01234",
    }

    class Client:
        def __init__(self):
            pass

        def get(self, _path, _params):
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["paper", "info", "2501.01234"]) == 0

    assert output.getvalue() == (
        "id: 2501.01234\n"
        "title: A Paper\n"
        "published: 2025-01-03\n"
        "authors: Ada Researcher, Grace Scientist\n"
        "citations: 42\n"
        "url: https://paperswithcode.co/paper/2501.01234\n"
        "\n"
        "## Abstract\n"
        "\n"
        "First line.\n"
        "Second line.\n"
        "\n"
        "## Paper lineage\n"
        "\n"
        "### Predecessors\n"
        "\n"
        "- None\n"
        "\n"
        "### Successors\n"
        "\n"
        "- None\n"
    )


def test_paper_info_renders_linked_lineage_sections(monkeypatch):
    payload = {
        "arxiv_id": "2501.01234",
        "title": "A Paper",
        "predecessor_papers": [
            {
                "title": "Earlier [Paper]",
                "route_identifier": "2401.00001",
                "published": "2024-01-02",
            }
        ],
        "successor_papers": [
            {
                "title": "Later Paper",
                "route_identifier": "2601.00001",
                "published": None,
            }
        ],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, _path, _params):
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["paper", "info", "2501.01234"]) == 0

    assert output.getvalue().endswith(
        "\n## Paper lineage\n\n"
        "### Predecessors\n\n"
        "- [Earlier \\[Paper\\]](https://paperswithcode.co/paper/2401.00001)"
        " (2024-01-02)\n\n"
        "### Successors\n\n"
        "- [Later Paper](https://paperswithcode.co/paper/2601.00001)\n"
    )


def test_paper_info_renders_linked_resources_when_requested(monkeypatch):
    calls = []
    payload = {
        "arxiv_id": "2501.01234",
        "title": "A Paper",
        "repositories": [
            {"url": "https://github.com/example/implementation", "is_official": False},
            {"url": "https://github.com/example/official", "is_official": True},
        ],
        "project_pages": [
            {"url": "https://example.org/community", "is_official": False},
            {"url": "https://example.org/project", "is_official": True},
        ],
        "hf_models": ["https://huggingface.co/example/model"],
        "hf_datasets": ["https://huggingface.co/datasets/example/data"],
        "hf_spaces": ["https://huggingface.co/spaces/example/demo"],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path, params):
            calls.append((path, params))
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["paper", "info", "2501.01234", "--include-resources"]) == 0

    assert calls == [
        ("papers/2501.01234", {"include_resources": True}),
    ]
    assert output.getvalue() == (
        "id: 2501.01234\n"
        "title: A Paper\n"
        "\n"
        "## Paper lineage\n"
        "\n"
        "### Predecessors\n"
        "\n"
        "- None\n"
        "\n"
        "### Successors\n"
        "\n"
        "- None\n"
        "\n"
        "## Repositories\n"
        "\n"
        "- **Official:** https://github.com/example/official\n"
        "- https://github.com/example/implementation\n"
        "\n"
        "## Project pages\n"
        "\n"
        "- **Official:** https://example.org/project\n"
        "- https://example.org/community\n"
        "\n"
        "## Hugging Face artifacts\n"
        "\n"
        "- **Model:** https://huggingface.co/example/model\n"
        "- **Dataset:** https://huggingface.co/datasets/example/data\n"
        "- **Space:** https://huggingface.co/spaces/example/demo\n"
    )


def test_paper_lineage_renders_linked_markdown_sections(monkeypatch):
    payload = {
        "paper": {
            "reference": "2307.09288",
            "title": "Llama 2: Open Foundation and Fine-Tuned Chat Models",
        },
        "predecessors": [
            {
                "reference": "2302.13971",
                "title": "LLaMA: Open and Efficient Foundation Language Models",
            }
        ],
        "successors": [],
    }

    class Client:
        def __init__(self):
            pass

        def get(self, path):
            assert path == "research/papers/2307.09288/lineage"
            return Response(json.dumps(payload).encode(), {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["paper", "lineage", "list", "2307.09288"]) == 0

    assert output.getvalue() == (
        "# Paper lineage\n"
        "\n"
        "## Paper\n"
        "\n"
        "- [Llama 2: Open Foundation and Fine-Tuned Chat Models]"
        "(https://paperswithcode.co/paper/2307.09288) (`2307.09288`)\n"
        "\n"
        "## Predecessors\n"
        "\n"
        "- [LLaMA: Open and Efficient Foundation Language Models]"
        "(https://paperswithcode.co/paper/2302.13971) (`2302.13971`)\n"
        "\n"
        "## Successors\n"
        "\n"
        "- None\n"
    )


def test_json_schema_and_errors_use_stable_streams(monkeypatch):
    class Client:
        def __init__(self):
            pass

        def get(self, _path, _params):
            return Response(b'{"count":0,"results":[]}', {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    errors = io.StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        assert main(["search", "none", "--json"]) == 0
    assert json.loads(output.getvalue())["schema_version"] == "v1"
    assert errors.getvalue() == ""


def test_paper_read_supports_markdown_and_versioned_json(monkeypatch):
    paths = []

    class Client:
        def __init__(self):
            pass

        def get(self, path, params=None):
            paths.append((path, params))
            return Response(b"# Paper\n\nBody", {})

    monkeypatch.setattr("pwc_cli.cli.Client", Client)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["paper", "read", "2501.01234"]) == 0
    assert output.getvalue() == "# Paper\n\nBody\n"

    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["paper", "read", "2501.01234", "--json"]) == 0
    payload = json.loads(output.getvalue())
    assert payload == {
        "schema_version": "v1",
        "data": {"paper": "2501.01234", "markdown": "# Paper\n\nBody"},
    }
    assert paths == [
        ("research/papers/2501.01234/read", None),
        ("research/papers/2501.01234/read", None),
    ]


def test_transport_is_anonymous_by_default(monkeypatch):
    requests = []

    class HTTPResponse(io.BytesIO):
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(request, *, timeout):
        requests.append(request)
        assert timeout == 30
        return HTTPResponse(b"# Public paper\n")

    monkeypatch.delenv("PWC_API_URL", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    response = Client("https://example.test/api/v1").get(
        "research/papers/2501.01234/read"
    )

    assert response.body == b"# Public paper\n"
    assert requests[0].full_url.endswith("/api/v1/research/papers/2501.01234/read")
    assert requests[0].get_header("Authorization") is None
