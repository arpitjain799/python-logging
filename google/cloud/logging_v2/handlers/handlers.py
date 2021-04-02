# Copyright 2016 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Python :mod:`logging` handlers for Cloud Logging."""

import logging

from google.cloud.logging_v2.handlers.transports import BackgroundThreadTransport
from google.cloud.logging_v2.handlers._monitored_resources import detect_resource
from google.cloud.logging_v2.handlers._helpers import get_request_data

DEFAULT_LOGGER_NAME = "python"

EXCLUDED_LOGGER_DEFAULTS = (
    "google.cloud",
    "google.auth",
    "google_auth_httplib2",
    "werkzeug",
)

_CLEAR_HANDLER_RESOURCE_TYPES = ("gae_app", "cloud_function")



class CloudLoggingFilter(logging.Filter):
    """Python standard ``logging`` Filter class to add Cloud Logging
    information to each LogRecord.

    When attached to a LogHandler, each incoming log will receive trace and
    http_request related to the request. This data can be overwritten using
    the `extras` argument when writing logs.
    """

    def __init__(self, project=None, resource=None, labels=None):
        self.project = project
        self.resource = resource
        self.default_labels = labels if labels else {}

    def filter(self, record):
        inferred_http, inferred_trace = get_request_data()
        if inferred_trace is not None and self.project is not None:
            inferred_trace = f"projects/{self.project}/traces/{inferred_trace}"

        record.resource = getattr(record, "resource", self.resource) or ""
        record.trace = getattr(record, "trace", inferred_trace) or ""
        record.span_id = getattr(record, "spanId", "")
        record.http_request = getattr(record, "httpRequest", inferred_http) or {}
        record.request_method = record.http_request.get("requestMethod", "")
        record.request_url = record.http_request.get("requestUrl", "")
        record.user_agent = record.http_request.get("userAgent", "")
        record.protocol = record.http_request.get("protocol", "")
        record.labels = getattr(record, "labels", {})
        record.labels.update(self.default_labels)
        return True


class CloudLoggingHandler(logging.StreamHandler):
    """Handler that directly makes Cloud Logging API calls.

    This is a Python standard ``logging`` handler using that can be used to
    route Python standard logging messages directly to the Stackdriver
    Logging API.

    This handler is used when not in GAE or GKE environment.

    This handler supports both an asynchronous and synchronous transport.

    Example:

    .. code-block:: python

        import logging
        import google.cloud.logging
        from google.cloud.logging_v2.handlers import CloudLoggingHandler

        client = google.cloud.logging.Client()
        handler = CloudLoggingHandler(client)

        cloud_logger = logging.getLogger('cloudLogger')
        cloud_logger.setLevel(logging.INFO)
        cloud_logger.addHandler(handler)

        cloud_logger.error('bad news')  # API call
    """

    def __init__(
        self,
        client,
        *,
        name=DEFAULT_LOGGER_NAME,
        transport=BackgroundThreadTransport,
        resource=None,
        labels=None,
        stream=None,
    ):
        """
        Args:
            client (~logging_v2.client.Client):
                The authenticated Google Cloud Logging client for this
                handler to use.
            name (str): the name of the custom log in Cloud Logging.
                Defaults to 'python'. The name of the Python logger will be represented
                in the ``python_logger`` field.
            transport (~logging_v2.transports.Transport):
                Class for creating new transport objects. It should
                extend from the base :class:`.Transport` type and
                implement :meth`.Transport.send`. Defaults to
                :class:`.BackgroundThreadTransport`. The other
                option is :class:`.SyncTransport`.
            resource (~logging_v2.resource.Resource):
                Resource for this Handler. If not given, will be inferred from the environment.
            labels (Optional[dict]): Monitored resource of the entry, defaults
                to the global resource type.
            stream (Optional[IO]): Stream to be used by the handler.
        """
        super(CloudLoggingHandler, self).__init__(stream)
        self.name = name
        self.client = client
        self.transport = transport(client, name)

        if not resource:
            # infer the correct monitored resource from the local environment
            resource = detect_resource(project)

        self.addFilter(CloudLoggingFilter(client.project, resource, labels))

    def emit(self, record):
        """Actually log the specified logging record.

        Overrides the default emit behavior of ``StreamHandler``.

        See https://docs.python.org/2/library/logging.html#handler-objects

        Args:
            record (logging.LogRecord): The record to be logged.
        """
        message = super(CloudLoggingHandler, self).format(record)
        # send off request
        self.transport.send(
            record,
            message,
            resource=record.resource if record.resource else None,
            labels=record.labels if len(record.labels) > 0 else None,
            trace=record.trace if record.trace else None,
            span_id=record.spanId if record.spanId else None,
            http_request=record.httpRequest if len(record.httpRequest) > 0 else None,
        )


def setup_logging(
    handler, *, excluded_loggers=EXCLUDED_LOGGER_DEFAULTS, log_level=logging.INFO
):
    """Attach a logging handler to the Python root logger

    Excludes loggers that this library itself uses to avoid
    infinite recursion.

    Example:

    .. code-block:: python

        import logging
        import google.cloud.logging
        from google.cloud.logging_v2.handlers import CloudLoggingHandler

        client = google.cloud.logging.Client()
        handler = CloudLoggingHandler(client)
        google.cloud.logging.handlers.setup_logging(handler)
        logging.getLogger().setLevel(logging.DEBUG)

        logging.error('bad news')  # API call

    Args:
        handler (logging.handler): the handler to attach to the global handler
        excluded_loggers (Optional[Tuple[str]]): The loggers to not attach the handler
            to. This will always include the loggers in the
            path of the logging client itself.
        log_level (Optional[int]): Python logging log level. Defaults to
            :const:`logging.INFO`.
    """
    all_excluded_loggers = set(excluded_loggers + EXCLUDED_LOGGER_DEFAULTS)
    logger = logging.getLogger()

    # remove built-in handlers on App Engine or Cloud Functions environments
    if detect_resource().type in _CLEAR_HANDLER_RESOURCE_TYPES:
        logger.handlers.clear()

    logger.setLevel(log_level)
    logger.addHandler(handler)
    for logger_name in all_excluded_loggers:
        logger = logging.getLogger(logger_name)
        logger.propagate = False
        logger.addHandler(logging.StreamHandler())
