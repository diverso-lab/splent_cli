"""check:infra — the rules that keep one feature's stack per product apart.

None of these break a single product. They break the first time two products
of one line run together, which is the day a product line does what it exists
for, and by then the symptom is a random host port or an intermittently wrong
answer rather than an error. So they are checked, and checked against the file
as written: ``docker compose config`` resolves ${X_HOST_PORT} to a number,
which is exactly what a hardcoded port looks like.
"""

import textwrap

import pytest

from splent_cli.commands.check.check_infra import _isolation_findings, _published


def write(tmp_path, compose: str, env_example: str | None = None):
    docker = tmp_path / "docker"
    docker.mkdir(exist_ok=True)
    path = docker / "docker-compose.dev.yml"
    path.write_text(textwrap.dedent(compose))
    if env_example is not None:
        (docker / ".env.example").write_text(textwrap.dedent(env_example))
    return str(path)


GOOD = """
    services:
      splent_feature_elasticsearch:
        image: elasticsearch:8
        ports:
          - "${ELASTICSEARCH_HOST_PORT}:9200"
        volumes:
          - elasticsearch_data:/usr/share/elasticsearch/data
        networks:
          - splent_network
    volumes:
      elasticsearch_data:
    networks:
      splent_network:
        external: true
        name: ${SPLENT_NETWORK:-splent_network}
"""

GOOD_ENV = "ELASTICSEARCH_HOST_PORT=9201\n"


def only(findings, severity):
    return [m for s, m in findings if s == severity]


class TestAFeatureThatFollowsTheRules:
    def test_nothing_is_reported(self, tmp_path):
        path = write(tmp_path, GOOD, GOOD_ENV)
        assert _isolation_findings("splent_feature_elasticsearch", path) == []


class TestContainerName:
    def test_it_is_refused(self, tmp_path):
        """Compose does not prefix container_name, so the second product to
        start collides with the first and Docker refuses outright."""
        path = write(
            tmp_path,
            GOOD.replace(
                "        image: elasticsearch:8",
                "        image: elasticsearch:8\n        container_name: elasticsearch",
            ),
            GOOD_ENV,
        )
        failures = only(_isolation_findings("es", path), "fail")
        assert any("container_name" in f for f in failures)


class TestALiteralHostPort:
    def test_it_is_refused(self, tmp_path):
        """The offset each product gets exists so two products of a line can
        run at once. A literal defeats it and the second product dies with
        'port is already allocated'."""
        path = write(
            tmp_path, GOOD.replace('"${ELASTICSEARCH_HOST_PORT}:9200"', '"9200:9200"')
        )
        failures = only(
            _isolation_findings("splent_feature_elasticsearch", path), "fail"
        )
        assert any("9200" in f for f in failures)

    def test_the_message_names_the_variable_to_use(self, tmp_path):
        path = write(
            tmp_path, GOOD.replace('"${ELASTICSEARCH_HOST_PORT}:9200"', '"9200:9200"')
        )
        failures = only(
            _isolation_findings("splent_feature_elasticsearch", path), "fail"
        )
        assert any("ELASTICSEARCH_HOST_PORT" in f for f in failures)

    def test_a_container_only_port_is_fine(self, tmp_path):
        """A bare "9200" publishes nothing on a fixed host port."""
        path = write(
            tmp_path,
            GOOD.replace('"${ELASTICSEARCH_HOST_PORT}:9200"', '"9200"'),
            GOOD_ENV,
        )
        assert _isolation_findings("es", path) == []


class TestTheVariableNeedsADefault:
    def test_a_port_variable_absent_from_env_example_is_refused(self, tmp_path):
        """Compose does not fail on an unset variable in a port mapping, it
        publishes on a random host port. That is the very failure the offset
        exists to prevent, arrived at silently."""
        path = write(tmp_path, GOOD, "SOMETHING_ELSE=1\n")
        failures = only(_isolation_findings("es", path), "fail")
        assert any("ELASTICSEARCH_HOST_PORT" in f for f in failures)

    def test_shipping_no_env_example_at_all_is_refused(self, tmp_path):
        path = write(tmp_path, GOOD)
        failures = only(_isolation_findings("es", path), "fail")
        assert any(".env.example" in f for f in failures)

    def test_a_variable_not_named_host_port_is_flagged(self, tmp_path):
        """product:env only offsets variables ending in _HOST_PORT, so any
        other name gives every product the same number."""
        path = write(
            tmp_path,
            GOOD.replace("${ELASTICSEARCH_HOST_PORT}", "${ELASTICSEARCH_PORT}"),
            "ELASTICSEARCH_PORT=9201\n",
        )
        warnings = only(_isolation_findings("es", path), "warn")
        assert any("_HOST_PORT" in w for w in warnings)


class TestVolumes:
    def test_a_writable_host_path_is_flagged(self, tmp_path):
        """Host paths are not prefixed per project, so every product writes
        into the same directory."""
        path = write(
            tmp_path,
            GOOD.replace(
                "      - elasticsearch_data:/usr/share/elasticsearch/data",
                "      - /srv/es:/usr/share/elasticsearch/data",
            ),
            GOOD_ENV,
        )
        warnings = only(_isolation_findings("es", path), "warn")
        assert any("host path" in w for w in warnings)

    def test_a_read_only_host_path_is_fine(self, tmp_path):
        """Configuration mounted read-only is shared on purpose and cannot
        drift between products."""
        path = write(
            tmp_path,
            GOOD.replace(
                "      - elasticsearch_data:/usr/share/elasticsearch/data",
                "      - ${NGINX_FEATURE_HOST_DIR}/conf:/etc/nginx/conf.d:ro",
            ),
            GOOD_ENV,
        )
        assert only(_isolation_findings("es", path), "warn") == []

    def test_a_named_volume_never_declared_is_flagged(self, tmp_path):
        path = write(
            tmp_path, GOOD.replace("      elasticsearch_data:\n", ""), GOOD_ENV
        )
        warnings = only(_isolation_findings("es", path), "warn")
        assert any("top-level volumes" in w for w in warnings)


class TestTheNetwork:
    def test_a_network_pinned_to_one_name_is_refused(self, tmp_path):
        """Every container gets a DNS alias equal to its service name, so two
        products on one network publish the same alias and each product's
        lookups answer from either container at random."""
        path = write(
            tmp_path,
            GOOD.replace("    name: ${SPLENT_NETWORK:-splent_network}\n", ""),
            GOOD_ENV,
        )
        failures = only(_isolation_findings("es", path), "fail")
        assert any("SPLENT_NETWORK" in f for f in failures)


class TestUnreadableFiles:
    def test_a_file_that_cannot_be_parsed_is_a_warning_not_a_crash(self, tmp_path):
        path = write(tmp_path, "services: [oh dear\n")
        assert only(_isolation_findings("es", path), "warn")

    def test_a_missing_file_is_a_warning_not_a_crash(self):
        assert only(_isolation_findings("es", "/nowhere/docker-compose.yml"), "warn")


@pytest.mark.parametrize(
    "entry,expected",
    [
        ("9200:9200", "9200"),
        ("127.0.0.1:9200:9200", "9200"),
        ("${ES_HOST_PORT}:9200", "${ES_HOST_PORT}"),
        ("${ES_HOST_PORT:-9201}:9200", "${ES_HOST_PORT:-9201}"),
        ("9200", None),
        ({"published": "9482", "target": 9200}, "9482"),
        ({"target": 9200}, None),
    ],
)
def test_the_host_side_is_read_from_the_right_end(entry, expected):
    """A ${VAR:-default} contains colons of its own, and the host may be an
    address, so the pieces are counted from the right."""
    assert _published(entry) == expected
