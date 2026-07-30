"""
Tests for splent_cli.services.compose

Pure Python functions — no mocking needed for most cases.
Only resolve_file() touches the filesystem, so we use tmp_path there, and
legacy_feature_stack_notice() asks docker, so its subprocess.run is patched.
"""

import subprocess
from unittest.mock import patch

from splent_cli.services import compose


# ---------------------------------------------------------------------------
# project_name()
# ---------------------------------------------------------------------------


class TestProjectName:
    def test_basic(self):
        assert compose.project_name("my_app", "dev") == "my_app_dev"

    def test_prod(self):
        assert compose.project_name("my_app", "prod") == "my_app_prod"

    def test_slash_replaced(self):
        assert compose.project_name("splent_io/auth", "dev") == "splent_io_auth_dev"

    def test_at_replaced(self):
        assert compose.project_name("auth@v1.0.0", "dev") == "auth_v1_0_0_dev"

    def test_dot_replaced(self):
        assert compose.project_name("auth.feature", "dev") == "auth_feature_dev"

    def test_combined_special_chars(self):
        result = compose.project_name("splent_io/auth@v1.0", "prod")
        assert "/" not in result
        assert "@" not in result
        assert "." not in result


# ---------------------------------------------------------------------------
# feature_project_name() — one feature stack per product
# ---------------------------------------------------------------------------


class TestFeatureProjectName:
    def test_contains_the_product(self):
        result = compose.feature_project_name(
            "splent_io/splent_feature_elasticsearch@v0.1.0", "egc_wiki", "dev"
        )
        assert result.startswith("egc_wiki_")
        assert result == "egc_wiki_splent_io_splent_feature_elasticsearch_dev"

    def test_two_products_differ_on_the_same_feature_version(self):
        """The whole point: egc and isia must not share a container or a volume."""
        ref = "splent_io/splent_feature_elasticsearch@v0.1.0"
        egc = compose.feature_project_name(ref, "egc_wiki", "dev")
        isia = compose.feature_project_name(ref, "isia_wiki", "dev")
        assert egc != isia

    def test_a_version_bump_keeps_the_same_stack(self):
        """And therefore the same volume.

        The version used to be in the name, and every bump of a feature then
        started a fresh stack with an empty volume while the old data sat
        stranded under the previous name. Releasing elasticsearch v0.1.1,
        whose only change was a comment, wiped a 1246-document search index.
        For a cache that is a reindex; for a feature that ships a database it
        is the data.
        """
        older = compose.feature_project_name(
            "splent_io/splent_feature_redis@v1.0.0", "egc_wiki", "dev"
        )
        newer = compose.feature_project_name(
            "splent_io/splent_feature_redis@v2.0.0", "egc_wiki", "dev"
        )
        assert older == newer

    def test_an_unpinned_feature_lands_in_the_same_place(self):
        """feature:unlock drops the @version, and unlocking a feature must
        not move a product onto a different volume."""
        pinned = compose.feature_project_name(
            "splent_io/splent_feature_redis@v1.0.0", "egc_wiki", "dev"
        )
        editable = compose.feature_project_name(
            "splent_io/splent_feature_redis", "egc_wiki", "dev"
        )
        assert pinned == editable

    def test_is_a_safe_compose_project_name(self):
        result = compose.feature_project_name(
            "splent_io/splent_feature_auth@v1.0", "my_app", "prod"
        )
        assert "/" not in result
        assert "@" not in result
        assert "." not in result

    def test_product_stack_name_is_unchanged(self):
        """Renaming the product's own stack would orphan every existing
        product container in every workspace, and it never had the bug."""
        assert compose.project_name("egc_wiki", "dev") == "egc_wiki_dev"

    def test_legacy_name_is_the_product_free_one(self):
        ref = "splent_io/splent_feature_elasticsearch@v0.1.0"
        assert compose.legacy_feature_project_name(ref, "dev") == compose.project_name(
            ref, "dev"
        )
        assert compose.legacy_feature_project_name(
            ref, "dev"
        ) != compose.feature_project_name(ref, "egc_wiki", "dev")


# ---------------------------------------------------------------------------
# env_file_args() and feature_compose_cmd()
# ---------------------------------------------------------------------------


class TestEnvFileArgs:
    def test_points_at_the_product_env_when_it_exists(self, tmp_path):
        docker_dir = tmp_path / "egc_wiki" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / ".env").write_text("ELASTICSEARCH_HOST_PORT=9604\n")

        assert compose.env_file_args(str(tmp_path / "egc_wiki")) == [
            "--env-file",
            str(docker_dir / ".env"),
        ]

    def test_omitted_when_the_file_does_not_exist(self, tmp_path):
        (tmp_path / "egc_wiki" / "docker").mkdir(parents=True)
        assert compose.env_file_args(str(tmp_path / "egc_wiki")) == []

    def test_omitted_when_the_docker_dir_does_not_exist(self, tmp_path):
        assert compose.env_file_args(str(tmp_path / "never_derived")) == []

    def test_product_env_file_returns_none_when_absent(self, tmp_path):
        assert compose.product_env_file(str(tmp_path / "egc_wiki")) is None


class TestFeatureComposeCmd:
    def test_carries_project_and_env_file(self, tmp_path):
        docker_dir = tmp_path / "egc_wiki" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / ".env").write_text("ELASTICSEARCH_HOST_PORT=9604\n")

        cmd = compose.feature_compose_cmd(
            "egc_wiki_splent_io_splent_feature_elasticsearch_v0_1_0_dev",
            "/feature/docker/docker-compose.dev.yml",
            str(tmp_path / "egc_wiki"),
        )

        assert cmd[:3] == ["docker", "compose", "-p"]
        assert cmd[3] == "egc_wiki_splent_io_splent_feature_elasticsearch_v0_1_0_dev"
        assert cmd[cmd.index("--env-file") + 1] == str(docker_dir / ".env")
        assert cmd[cmd.index("-f") + 1] == "/feature/docker/docker-compose.dev.yml"

    def test_omits_env_file_when_product_never_ran_product_env(self, tmp_path):
        (tmp_path / "egc_wiki" / "docker").mkdir(parents=True)

        cmd = compose.feature_compose_cmd(
            "egc_wiki_splent_io_splent_feature_redis_dev",
            "/feature/docker/docker-compose.dev.yml",
            str(tmp_path / "egc_wiki"),
        )

        assert "--env-file" not in cmd
        assert cmd == [
            "docker",
            "compose",
            "-p",
            "egc_wiki_splent_io_splent_feature_redis_dev",
            "-f",
            "/feature/docker/docker-compose.dev.yml",
        ]


# ---------------------------------------------------------------------------
# legacy_feature_stack_notice() — the orphans left by the old shared naming
# ---------------------------------------------------------------------------


def _docker_ps(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class TestLegacyFeatureStackNotice:
    REF = "splent_io/splent_feature_elasticsearch@v0.1.0"
    LEGACY = "splent_io_splent_feature_elasticsearch_v0_1_0_dev"

    def test_names_the_old_project_and_the_command_that_removes_it(self):
        with patch(
            "splent_cli.services.compose.subprocess.run",
            return_value=_docker_ps("abc123\n"),
        ):
            notice = compose.legacy_feature_stack_notice(
                self.REF, "dev", "/feature/docker/docker-compose.dev.yml"
            )

        assert notice is not None
        assert self.LEGACY in notice
        assert (
            f"docker compose -p {self.LEGACY} "
            "-f /feature/docker/docker-compose.dev.yml down" in notice
        )

    def test_silent_when_no_container_is_left(self):
        with patch(
            "splent_cli.services.compose.subprocess.run",
            return_value=_docker_ps(""),
        ):
            assert (
                compose.legacy_feature_stack_notice(self.REF, "dev", "any.yml") is None
            )

    def test_silent_when_docker_cannot_be_asked(self):
        """A courtesy notice must never be the reason a command fails."""
        with patch(
            "splent_cli.services.compose.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert (
                compose.legacy_feature_stack_notice(self.REF, "dev", "any.yml") is None
            )


# ---------------------------------------------------------------------------
# normalize_feature_ref()
# ---------------------------------------------------------------------------


class TestNormalizeFeatureRef:
    def test_bare_name_gets_default_namespace(self):
        assert (
            compose.normalize_feature_ref("splent_feature_auth")
            == "splent_io/splent_feature_auth"
        )

    def test_namespaced_unchanged(self):
        assert (
            compose.normalize_feature_ref("splent_io/splent_feature_auth")
            == "splent_io/splent_feature_auth"
        )

    def test_strips_features_prefix(self):
        result = compose.normalize_feature_ref("features/splent_io/splent_feature_auth")
        assert result == "splent_io/splent_feature_auth"

    def test_versioned_ref_with_namespace(self):
        result = compose.normalize_feature_ref("splent_io/splent_feature_auth@v1.0")
        assert result == "splent_io/splent_feature_auth@v1.0"


# ---------------------------------------------------------------------------
# resolve_file() — touches the filesystem
# ---------------------------------------------------------------------------


class TestResolveFile:
    def test_prefers_env_specific_file(self, tmp_path):
        docker_dir = tmp_path / "my_app" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / "docker-compose.dev.yml").touch()
        (docker_dir / "docker-compose.yml").touch()

        result = compose.resolve_file(str(tmp_path / "my_app"), "dev")
        assert result is not None
        assert "docker-compose.dev.yml" in result

    def test_falls_back_to_generic(self, tmp_path):
        docker_dir = tmp_path / "my_app" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / "docker-compose.yml").touch()

        result = compose.resolve_file(str(tmp_path / "my_app"), "dev")
        assert result is not None
        assert "docker-compose.yml" in result

    def test_returns_none_when_no_file(self, tmp_path):
        (tmp_path / "my_app" / "docker").mkdir(parents=True)
        result = compose.resolve_file(str(tmp_path / "my_app"), "dev")
        assert result is None

    def test_returns_none_when_no_docker_dir(self, tmp_path):
        (tmp_path / "my_app").mkdir()
        result = compose.resolve_file(str(tmp_path / "my_app"), "dev")
        assert result is None


# ---------------------------------------------------------------------------
# product_path() and feature_docker_dir()
# ---------------------------------------------------------------------------


class TestPaths:
    def test_product_path(self):
        result = compose.product_path("my_app", "/workspace")
        assert result == "/workspace/my_app"

    def test_feature_docker_dir(self):
        result = compose.feature_docker_dir(
            "/workspace", "splent_io/splent_feature_auth"
        )
        assert (
            result
            == "/workspace/.splent_cache/features/splent_io/splent_feature_auth/docker"
        )


# ---------------------------------------------------------------------------
# The other half of the isolation: a network per product
# ---------------------------------------------------------------------------
#
# A project name of its own gives each product its own containers. It does not
# give them their own names: Compose puts every container on the network under
# a DNS alias equal to its service name, so two products running the same
# feature publish the same alias on a shared network and each product's
# lookups answer from either container at random. Measured, not supposed: with
# two elasticsearch containers up, six lookups from one product's web
# container returned the other product's address twice.


class TestNetworkName:
    def test_it_is_derived_from_the_product(self):
        assert compose.network_name("egc_wiki") == "egc_wiki_network"

    def test_two_products_never_share_one(self):
        assert compose.network_name("egc_wiki") != compose.network_name("isia_wiki")


class TestEnsureNetwork:
    def test_an_existing_network_is_not_created_again(self):
        calls = []

        def fake(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess([], 0, "", "")

        with patch("splent_cli.services.compose.subprocess.run", side_effect=fake):
            assert compose.ensure_network("egc_wiki_network") is True

        assert calls[0][:3] == ["docker", "network", "inspect"]
        assert not any(cmd[:3] == ["docker", "network", "create"] for cmd in calls)

    def test_a_missing_network_is_created(self):
        calls = []

        def fake(cmd, **kwargs):
            calls.append(cmd)
            code = 1 if cmd[:3] == ["docker", "network", "inspect"] else 0
            return subprocess.CompletedProcess([], code, "", "")

        with patch("splent_cli.services.compose.subprocess.run", side_effect=fake):
            assert compose.ensure_network("egc_wiki_network") is True

        assert ["docker", "network", "create", "egc_wiki_network"] in calls

    def test_docker_being_unreachable_is_not_an_exception(self):
        """A convenience must never be the reason a command dies. Without the
        guard, a machine with no docker on PATH raised out of the middle of
        product:up instead of failing at the compose call with a message
        about docker."""
        with patch(
            "splent_cli.services.compose.subprocess.run",
            side_effect=OSError("no docker"),
        ):
            assert compose.ensure_network("egc_wiki_network") is False


# ---------------------------------------------------------------------------
# Which env file, and the deploy project name
# ---------------------------------------------------------------------------


class TestEnvFileFollowsTheEnvironment:
    def test_prod_reads_the_deploy_file(self, tmp_path):
        """product:env --merge --prod writes .env.deploy. Handing the
        development file to a production stack published nginx on the
        development port and gave the search node the development heap."""
        docker_dir = tmp_path / "egc_wiki" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / ".env").write_text("NGINX_HTTP_HOST_PORT=361\n")
        (docker_dir / ".env.deploy").write_text("NGINX_HTTP_HOST_PORT=80\n")

        product = str(tmp_path / "egc_wiki")
        assert compose.product_env_file(product, "dev") == str(docker_dir / ".env")
        assert compose.product_env_file(product, "prod") == str(
            docker_dir / ".env.deploy"
        )

    def test_a_missing_deploy_file_is_not_silently_the_dev_one(self, tmp_path):
        docker_dir = tmp_path / "egc_wiki" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / ".env").write_text("X=1\n")

        assert compose.env_file_args(str(tmp_path / "egc_wiki"), "prod") == []


class TestMissingEnvFileNotice:
    def test_it_warns_when_the_merge_never_ran(self, tmp_path):
        (tmp_path / "egc_wiki" / "docker").mkdir(parents=True)
        notice = compose.missing_env_file_notice(str(tmp_path / "egc_wiki"))
        assert notice is not None
        assert "product:env --merge" in notice

    def test_it_names_the_prod_command_in_prod(self, tmp_path):
        (tmp_path / "egc_wiki" / "docker").mkdir(parents=True)
        notice = compose.missing_env_file_notice(str(tmp_path / "egc_wiki"), "prod")
        assert ".env.deploy" in notice
        assert "--merge --prod" in notice

    def test_silent_when_the_file_is_there(self, tmp_path):
        docker_dir = tmp_path / "egc_wiki" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / ".env").write_text("X=1\n")
        assert compose.missing_env_file_notice(str(tmp_path / "egc_wiki")) is None


class TestDeployProjectName:
    def test_it_names_the_product(self):
        """Deploy used to pass no -p, so Compose named the project after the
        directory holding the compose file. That is 'docker' in every product,
        so deploying a second product to a host recreated the first one's
        containers."""
        assert compose.deploy_project_name("egc_wiki") == "egc_wiki_deploy"

    def test_two_products_on_one_host_do_not_collide(self):
        assert compose.deploy_project_name("egc_wiki") != compose.deploy_project_name(
            "isia_wiki"
        )


class TestLegacyDeployProjectNotice:
    def test_it_reports_containers_under_the_shared_project(self):
        with patch("splent_cli.services.compose.subprocess.run") as run:
            run.return_value = _docker_ps("abc123\ndef456\n")
            notice = compose.legacy_deploy_project_notice("egc_wiki")
        assert "2 container(s)" in notice
        assert "docker compose -p docker" in notice

    def test_it_counts_stopped_containers_too(self):
        """They still hold the volumes and the names the new project wants."""
        with patch("splent_cli.services.compose.subprocess.run") as run:
            run.return_value = _docker_ps("abc123\n")
            compose.legacy_deploy_project_notice("egc_wiki")
        assert "-aq" in run.call_args[0][0]

    def test_silent_when_there_is_nothing_to_report(self):
        with patch("splent_cli.services.compose.subprocess.run") as run:
            run.return_value = _docker_ps("")
            assert compose.legacy_deploy_project_notice("egc_wiki") is None

    def test_silent_when_docker_cannot_be_asked(self):
        with patch(
            "splent_cli.services.compose.subprocess.run",
            side_effect=OSError("no docker"),
        ):
            assert compose.legacy_deploy_project_notice("egc_wiki") is None


class TestAttachSelfToNetwork:
    """The CLI runs in a container and reaches product services by name.

    That worked while every product shared one network. With one network
    per product it has to join the right one, and just as importantly leave
    the others: a container on two product networks is back to one name
    answering from two servers, and that container is the one running
    feature:search reindex, which would rebuild an index into a sibling
    product's node without an error anywhere.
    """

    def _in_a_container(self, monkeypatch, networks):
        monkeypatch.setattr("os.path.exists", lambda p: p == "/.dockerenv")
        calls = []

        def fake(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["docker", "inspect"]:
                return subprocess.CompletedProcess([], 0, " ".join(networks), "")
            return subprocess.CompletedProcess([], 0, "", "")

        return calls, fake

    def test_it_joins_the_products_network(self, monkeypatch):
        calls, fake = self._in_a_container(monkeypatch, ["splent_network"])
        with patch("splent_cli.services.compose.subprocess.run", side_effect=fake):
            assert compose.attach_self_to_network("egc_wiki_network") is True
        assert any(
            cmd[:4] == ["docker", "network", "connect", "egc_wiki_network"]
            for cmd in calls
        )

    def test_it_leaves_another_products_network(self, monkeypatch):
        calls, fake = self._in_a_container(
            monkeypatch, ["splent_network", "isia_wiki_network"]
        )
        with patch("splent_cli.services.compose.subprocess.run", side_effect=fake):
            compose.attach_self_to_network("egc_wiki_network")
        assert any(
            cmd[:4] == ["docker", "network", "disconnect", "isia_wiki_network"]
            for cmd in calls
        )

    def test_it_stays_on_the_shared_network(self, monkeypatch):
        """That is where products written before this rule still live, and
        where the CLI's own compose file put it."""
        calls, fake = self._in_a_container(monkeypatch, ["splent_network"])
        with patch("splent_cli.services.compose.subprocess.run", side_effect=fake):
            compose.attach_self_to_network("egc_wiki_network")
        assert not any(
            cmd[:4] == ["docker", "network", "disconnect", "splent_network"]
            for cmd in calls
        )

    def test_already_attached_is_not_attached_again(self, monkeypatch):
        calls, fake = self._in_a_container(
            monkeypatch, ["splent_network", "egc_wiki_network"]
        )
        with patch("splent_cli.services.compose.subprocess.run", side_effect=fake):
            assert compose.attach_self_to_network("egc_wiki_network") is True
        assert not any(cmd[2] == "connect" for cmd in calls if len(cmd) > 2)

    def test_running_on_the_host_does_nothing(self, monkeypatch):
        monkeypatch.setattr("os.path.exists", lambda p: False)
        with patch("splent_cli.services.compose.subprocess.run") as run:
            assert compose.attach_self_to_network("egc_wiki_network") is False
        run.assert_not_called()

    def test_docker_being_unreachable_is_not_an_exception(self, monkeypatch):
        monkeypatch.setattr("os.path.exists", lambda p: p == "/.dockerenv")
        with patch(
            "splent_cli.services.compose.subprocess.run",
            side_effect=OSError("no docker"),
        ):
            assert compose.attach_self_to_network("egc_wiki_network") is False


class TestSupersededVersionNotice:
    """What the version-in-the-name era left behind.

    Project names used to carry the pinned version, so every bump started a
    fresh stack: the old one kept the host port and kept the data. The
    version is gone from the name now and this finds the leftovers.
    """

    def _projects(self, *names):
        return subprocess.CompletedProcess([], 0, "\n".join(names) + "\n", "")

    def test_it_names_the_stacks_left_behind(self):
        with patch("splent_cli.services.compose.subprocess.run") as run:
            run.return_value = self._projects(
                "egc_wiki_splent_io_splent_feature_elasticsearch_v0_1_0_dev",
                "egc_wiki_splent_io_splent_feature_elasticsearch_v0_1_1_dev",
                # The current one, which is the version-free name.
                "egc_wiki_splent_io_splent_feature_elasticsearch_dev",
            )
            notice = compose.superseded_version_notice(
                "splent_io/splent_feature_elasticsearch@v0.1.1", "egc_wiki", "dev"
            )
        assert "v0_1_0" in notice
        assert "v0_1_1" in notice
        # And never the one about to run, or the notice would tell somebody
        # to stop the stack they just started.
        assert (
            "-p egc_wiki_splent_io_splent_feature_elasticsearch_dev down" not in notice
        )

    def test_another_products_stack_is_not_this_products_problem(self):
        with patch("splent_cli.services.compose.subprocess.run") as run:
            run.return_value = self._projects(
                "isia_wiki_splent_io_splent_feature_elasticsearch_v0_1_0_dev"
            )
            assert (
                compose.superseded_version_notice(
                    "splent_io/splent_feature_elasticsearch@v0.1.1", "egc_wiki", "dev"
                )
                is None
            )

    def test_the_other_environment_is_left_alone(self):
        with patch("splent_cli.services.compose.subprocess.run") as run:
            run.return_value = self._projects(
                "egc_wiki_splent_io_splent_feature_elasticsearch_v0_1_0_prod"
            )
            assert (
                compose.superseded_version_notice(
                    "splent_io/splent_feature_elasticsearch@v0.1.1", "egc_wiki", "dev"
                )
                is None
            )

    def test_silent_when_only_the_current_stack_is_there(self):
        with patch("splent_cli.services.compose.subprocess.run") as run:
            run.return_value = self._projects(
                "egc_wiki_splent_io_splent_feature_elasticsearch_dev"
            )
            assert (
                compose.superseded_version_notice(
                    "splent_io/splent_feature_elasticsearch@v0.1.1", "egc_wiki", "dev"
                )
                is None
            )

    def test_it_counts_stopped_containers_too(self):
        """A stopped container still holds nothing, but a stopped stack is
        about to be started again by somebody reading this."""
        with patch("splent_cli.services.compose.subprocess.run") as run:
            run.return_value = self._projects("")
            compose.superseded_version_notice(
                "splent_io/splent_feature_elasticsearch@v0.1.1", "egc_wiki", "dev"
            )
        assert "-a" in run.call_args[0][0]

    def test_docker_being_unreachable_is_not_an_exception(self):
        with patch(
            "splent_cli.services.compose.subprocess.run",
            side_effect=OSError("no docker"),
        ):
            assert (
                compose.superseded_version_notice(
                    "splent_io/splent_feature_elasticsearch@v0.1.1", "egc_wiki", "dev"
                )
                is None
            )
