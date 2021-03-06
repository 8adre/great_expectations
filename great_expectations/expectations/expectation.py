import logging
import re
from abc import ABC, ABCMeta, abstractmethod
from collections import Counter
from copy import deepcopy
from inspect import isabstract
from typing import Any, Callable, Dict, Iterable, List, Optional, Type, Union

from great_expectations import __version__ as ge_version
from great_expectations.core.expectation_configuration import (
    ExpectationConfiguration,
    parse_result_format,
)
from great_expectations.core.expectation_validation_result import (
    ExpectationValidationResult,
)
from great_expectations.exceptions import (
    GreatExpectationsError,
    InvalidExpectationConfigurationError,
    InvalidExpectationKwargsError,
)
from great_expectations.expectations.registry import (
    get_metric_kwargs,
    register_expectation,
    register_renderer,
)
from great_expectations.expectations.util import legacy_method_parameters

from ..core.util import convert_to_json_serializable, nested_update
from ..data_asset.util import recursively_convert_to_json_serializable
from ..execution_engine import ExecutionEngine, PandasExecutionEngine
from ..render.renderer.renderer import renderer
from ..render.types import (
    CollapseContent,
    RenderedStringTemplateContent,
    RenderedTableContent,
)
from ..render.util import num_to_str
from ..validator.validation_graph import MetricConfiguration

logger = logging.getLogger(__name__)


p1 = re.compile(r"(.)([A-Z][a-z]+)")
p2 = re.compile(r"([a-z0-9])([A-Z])")


def camel_to_snake(name):
    name = p1.sub(r"\1_\2", name)
    return p2.sub(r"\1_\2", name).lower()


class MetaExpectation(ABCMeta):
    """MetaExpectation registers Expectations as they are defined, adding them to the Expectation registry.

    Any class inheriting from Expectation will be registered based on the value of the "expectation_type" class
    attribute, or, if that is not set, by snake-casing the name of the class.
    """

    def __new__(cls, clsname, bases, attrs):
        newclass = super().__new__(cls, clsname, bases, attrs)
        if not isabstract(newclass):
            newclass.expectation_type = camel_to_snake(clsname)
            register_expectation(newclass)
        newclass._register_renderer_functions()
        default_kwarg_values = dict()
        for base in reversed(bases):
            default_kwargs = getattr(base, "default_kwarg_values", dict())
            default_kwarg_values = nested_update(default_kwarg_values, default_kwargs)

        newclass.default_kwarg_values = nested_update(
            default_kwarg_values, attrs.get("default_kwarg_values", dict())
        )
        return newclass


class Expectation(ABC, metaclass=MetaExpectation):
    """Base class for all Expectations.

    Expectation classes *must* have the following attributes set:
        1. `domain_keys`: a tuple of the *keys* used to determine the domain of the
           expectation
        2. `success_keys`: a tuple of the *keys* used to determine the success of
           the expectation.

    In some cases, subclasses of Expectation (such as TableExpectation) can
    inherit these properties from their parent class.

    They *may* optionally override `runtime_keys` and `default_kwarg_values`, and
    may optionally set an explicit value for expectation_type.
        1. runtime_keys lists the keys that can be used to control output but will
           not affect the actual success value of the expectation (such as result_format).
        2. default_kwarg_values is a dictionary that will be used to fill unspecified
           kwargs from the Expectation Configuration.

    Expectation classes *must* implement the following:
        1. `_validate`
        2. `get_validation_dependencies`

    In some cases, subclasses of Expectation, such as ColumnMapExpectation will already
    have correct implementations that may simply be inherited.

    Additionally, they *may* provide implementations of:
        1. `validate_configuration`, which should raise an error if the configuration
           will not be usable for the Expectation
        2. Data Docs rendering methods decorated with the @renderer decorator. See the


    """

    version = ge_version
    domain_keys = tuple()
    success_keys = tuple()
    runtime_keys = (
        "include_config",
        "catch_exceptions",
        "result_format",
    )
    default_kwarg_values = {
        "include_config": True,
        "catch_exceptions": False,
        "result_format": "BASIC",
    }
    legacy_method_parameters = legacy_method_parameters

    def __init__(self, configuration: Optional[ExpectationConfiguration] = None):
        if configuration is not None:
            self.validate_configuration(configuration)
        self._configuration = configuration

    @classmethod
    def _register_renderer_functions(cls):
        expectation_type = camel_to_snake(cls.__name__)

        for candidate_renderer_fn_name in dir(cls):
            attr_obj = getattr(cls, candidate_renderer_fn_name)
            if not hasattr(attr_obj, "_renderer_type"):
                continue
            register_renderer(
                object_name=expectation_type, parent_class=cls, renderer_fn=attr_obj
            )

    @abstractmethod
    def get_validation_dependencies(
        self,
        configuration: Optional[ExpectationConfiguration] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        runtime_configuration: Optional[dict] = None,
    ):
        raise NotImplementedError

    @abstractmethod
    def _validate(
        self,
        configuration: ExpectationConfiguration,
        metrics: Dict,
        runtime_configuration: dict = None,
        execution_engine: ExecutionEngine = None,
    ):
        raise NotImplementedError

    @classmethod
    @renderer(renderer_type="renderer.prescriptive")
    def _prescriptive_renderer(
        cls,
        configuration=None,
        result=None,
        language=None,
        runtime_configuration=None,
        **kwargs,
    ):
        return [
            RenderedStringTemplateContent(
                **{
                    "content_block_type": "string_template",
                    "styling": {"parent": {"classes": ["alert", "alert-warning"]}},
                    "string_template": {
                        "template": "$expectation_type(**$kwargs)",
                        "params": {
                            "expectation_type": configuration.expectation_type,
                            "kwargs": configuration.kwargs,
                        },
                        "styling": {
                            "params": {
                                "expectation_type": {
                                    "classes": ["badge", "badge-warning"],
                                }
                            }
                        },
                    },
                }
            )
        ]

    @classmethod
    @renderer(renderer_type="renderer.diagnostic.status_icon")
    def _diagnostic_status_icon_renderer(
        cls,
        configuration=None,
        result=None,
        language=None,
        runtime_configuration=None,
        **kwargs,
    ):
        assert result, "Must provide a result object."
        if result.exception_info["raised_exception"]:
            return RenderedStringTemplateContent(
                **{
                    "content_block_type": "string_template",
                    "string_template": {
                        "template": "$icon",
                        "params": {"icon": "", "markdown_status_icon": "❗"},
                        "styling": {
                            "params": {
                                "icon": {
                                    "classes": [
                                        "fas",
                                        "fa-exclamation-triangle",
                                        "text-warning",
                                    ],
                                    "tag": "i",
                                }
                            }
                        },
                    },
                }
            )

        if result.success:
            return RenderedStringTemplateContent(
                **{
                    "content_block_type": "string_template",
                    "string_template": {
                        "template": "$icon",
                        "params": {"icon": "", "markdown_status_icon": "✅"},
                        "styling": {
                            "params": {
                                "icon": {
                                    "classes": [
                                        "fas",
                                        "fa-check-circle",
                                        "text-success",
                                    ],
                                    "tag": "i",
                                }
                            }
                        },
                    },
                    "styling": {
                        "parent": {
                            "classes": ["hide-succeeded-validation-target-child"]
                        }
                    },
                }
            )
        else:
            return RenderedStringTemplateContent(
                **{
                    "content_block_type": "string_template",
                    "string_template": {
                        "template": "$icon",
                        "params": {"icon": "", "markdown_status_icon": "❌"},
                        "styling": {
                            "params": {
                                "icon": {
                                    "tag": "i",
                                    "classes": ["fas", "fa-times", "text-danger"],
                                }
                            }
                        },
                    },
                }
            )

    @classmethod
    @renderer(renderer_type="renderer.diagnostic.unexpected_statement")
    def _diagnostic_unexpected_statement_renderer(
        cls,
        configuration=None,
        result=None,
        language=None,
        runtime_configuration=None,
        **kwargs,
    ):
        assert result, "Must provide a result object."
        success = result.success
        result_dict = result.result

        if result.exception_info["raised_exception"]:
            exception_message_template_str = (
                "\n\n$expectation_type raised an exception:\n$exception_message"
            )

            exception_message = RenderedStringTemplateContent(
                **{
                    "content_block_type": "string_template",
                    "string_template": {
                        "template": exception_message_template_str,
                        "params": {
                            "expectation_type": result.expectation_config.expectation_type,
                            "exception_message": result.exception_info[
                                "exception_message"
                            ],
                        },
                        "tag": "strong",
                        "styling": {
                            "classes": ["text-danger"],
                            "params": {
                                "exception_message": {"tag": "code"},
                                "expectation_type": {
                                    "classes": ["badge", "badge-danger", "mb-2"]
                                },
                            },
                        },
                    },
                }
            )

            exception_traceback_collapse = CollapseContent(
                **{
                    "collapse_toggle_link": "Show exception traceback...",
                    "collapse": [
                        RenderedStringTemplateContent(
                            **{
                                "content_block_type": "string_template",
                                "string_template": {
                                    "template": result.exception_info[
                                        "exception_traceback"
                                    ],
                                    "tag": "code",
                                },
                            }
                        )
                    ],
                }
            )

            return [exception_message, exception_traceback_collapse]

        if success or not result_dict.get("unexpected_count"):
            return []
        else:
            unexpected_count = num_to_str(
                result_dict["unexpected_count"], use_locale=True, precision=20
            )
            unexpected_percent = (
                num_to_str(result_dict["unexpected_percent"], precision=4) + "%"
            )
            element_count = num_to_str(
                result_dict["element_count"], use_locale=True, precision=20
            )

            template_str = (
                "\n\n$unexpected_count unexpected values found. "
                "$unexpected_percent of $element_count total rows."
            )

            return [
                RenderedStringTemplateContent(
                    **{
                        "content_block_type": "string_template",
                        "string_template": {
                            "template": template_str,
                            "params": {
                                "unexpected_count": unexpected_count,
                                "unexpected_percent": unexpected_percent,
                                "element_count": element_count,
                            },
                            "tag": "strong",
                            "styling": {"classes": ["text-danger"]},
                        },
                    }
                )
            ]

    @classmethod
    @renderer(renderer_type="renderer.diagnostic.unexpected_table")
    def _diagnostic_unexpected_table_renderer(
        cls,
        configuration=None,
        result=None,
        language=None,
        runtime_configuration=None,
        **kwargs,
    ):
        try:
            result_dict = result.result
        except KeyError:
            return None

        if result_dict is None:
            return None

        if not result_dict.get("partial_unexpected_list") and not result_dict.get(
            "partial_unexpected_counts"
        ):
            return None

        table_rows = []

        if result_dict.get("partial_unexpected_counts"):
            # We will check to see whether we have *all* of the unexpected values
            # accounted for in our count, and include counts if we do. If we do not,
            # we will use this as simply a better (non-repeating) source of
            # "sampled" unexpected values
            total_count = 0
            for unexpected_count_dict in result_dict.get("partial_unexpected_counts"):
                if not isinstance(unexpected_count_dict, dict):
                    # handles case: "partial_exception_counts requires a hashable type"
                    # this case is also now deprecated (because the error is moved to an errors key
                    # the error also *should have* been updated to "partial_unexpected_counts ..." long ago.
                    # NOTE: JPC 20200724 - Consequently, this codepath should be removed by approximately Q1 2021
                    continue
                value = unexpected_count_dict.get("value")
                count = unexpected_count_dict.get("count")
                total_count += count
                if value is not None and value != "":
                    table_rows.append([value, count])
                elif value == "":
                    table_rows.append(["EMPTY", count])
                else:
                    table_rows.append(["null", count])

            # Check to see if we have *all* of the unexpected values accounted for. If so,
            # we show counts. If not, we only show "sampled" unexpected values.
            if total_count == result_dict.get("unexpected_count"):
                header_row = ["Unexpected Value", "Count"]
            else:
                header_row = ["Sampled Unexpected Values"]
                table_rows = [[row[0]] for row in table_rows]
        else:
            header_row = ["Sampled Unexpected Values"]
            sampled_values_set = set()
            for unexpected_value in result_dict.get("partial_unexpected_list"):
                if unexpected_value:
                    string_unexpected_value = str(unexpected_value)
                elif unexpected_value == "":
                    string_unexpected_value = "EMPTY"
                else:
                    string_unexpected_value = "null"
                if string_unexpected_value not in sampled_values_set:
                    table_rows.append([unexpected_value])
                    sampled_values_set.add(string_unexpected_value)

        unexpected_table_content_block = RenderedTableContent(
            **{
                "content_block_type": "table",
                "table": table_rows,
                "header_row": header_row,
                "styling": {
                    "body": {"classes": ["table-bordered", "table-sm", "mt-3"]}
                },
            }
        )

        return unexpected_table_content_block

    @classmethod
    @renderer(renderer_type="renderer.diagnostic.observed_value")
    def _diagnostic_observed_value_renderer(
        cls,
        configuration=None,
        result=None,
        language=None,
        runtime_configuration=None,
        **kwargs,
    ):
        result_dict = result.result
        if result_dict is None:
            return "--"

        if result_dict.get("observed_value"):
            observed_value = result_dict.get("observed_value")
            if isinstance(observed_value, (int, float)) and not isinstance(
                observed_value, bool
            ):
                return num_to_str(observed_value, precision=10, use_locale=True)
            return str(observed_value)
        elif result_dict.get("unexpected_percent") is not None:
            return (
                num_to_str(result_dict.get("unexpected_percent"), precision=5)
                + "% unexpected"
            )
        else:
            return "--"

    @classmethod
    def get_allowed_config_keys(cls):
        return cls.domain_keys + cls.success_keys + cls.runtime_keys

    def _validate(
        self,
        configuration: ExpectationConfiguration,
        metrics: Dict[str, Any],
        runtime_configuration: Dict,
        execution_engine: ExecutionEngine,
    ):
        raise NotImplementedError

    def metrics_validate(
        self,
        metrics: Dict,
        configuration: Optional[ExpectationConfiguration] = None,
        runtime_configuration: dict = None,
        execution_engine: ExecutionEngine = None,
    ) -> "ExpectationValidationResult":
        if configuration is None:
            configuration = self.configuration
        provided_metrics = dict()
        requested_metrics = self.get_validation_dependencies(
            configuration,
            execution_engine=execution_engine,
            runtime_configuration=runtime_configuration,
        )["metrics"]
        for name, metric_edge_key in requested_metrics.items():
            provided_metrics[name] = metrics[metric_edge_key.id]

        return self._build_evr(
            self._validate(
                configuration=configuration,
                metrics=provided_metrics,
                runtime_configuration=runtime_configuration,
                execution_engine=execution_engine,
            ),
            configuration,
        )

    def _build_evr(self, raw_response, configuration):
        """_build_evr is a lightweight convenience wrapper handling cases where an Expectation implementor
        fails to return an EVR but returns the necessary components in a dictionary."""
        if not isinstance(raw_response, ExpectationValidationResult):
            if isinstance(raw_response, dict):
                evr = ExpectationValidationResult(**raw_response)
                evr.expectation_config = configuration
            else:
                raise GreatExpectationsError("Unable to build EVR")
        else:
            evr = raw_response
            evr.expectation_config = configuration
        return evr

    def get_validation_dependencies(
        self,
        configuration: Optional[ExpectationConfiguration] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        runtime_configuration: Optional[dict] = None,
    ):
        """Returns the result format and metrics required to validate this Expectation using the provided result format."""
        return {
            "result_format": parse_result_format(
                self.get_runtime_kwargs(
                    configuration=configuration,
                    runtime_configuration=runtime_configuration,
                ).get("result_format")
            ),
            "metrics": dict(),
        }

    def get_domain_kwargs(
        self, configuration: Optional[ExpectationConfiguration] = None
    ):
        if not configuration:
            configuration = self.configuration

        domain_kwargs = {
            key: configuration.kwargs.get(key, self.default_kwarg_values.get(key))
            for key in self.domain_keys
        }
        # Process evaluation parameter dependencies

        missing_kwargs = set(self.domain_keys) - set(domain_kwargs.keys())
        if missing_kwargs:
            raise InvalidExpectationKwargsError(
                f"Missing domain kwargs: {list(missing_kwargs)}"
            )
        return domain_kwargs

    def get_success_kwargs(
        self, configuration: Optional[ExpectationConfiguration] = None
    ):
        if not configuration:
            configuration = self.configuration

        domain_kwargs = self.get_domain_kwargs(configuration)
        success_kwargs = {
            key: configuration.kwargs.get(key, self.default_kwarg_values.get(key))
            for key in self.success_keys
        }
        success_kwargs.update(domain_kwargs)
        return success_kwargs

    def get_runtime_kwargs(
        self,
        configuration: Optional[ExpectationConfiguration] = None,
        runtime_configuration: dict = None,
    ):
        if not configuration:
            configuration = self.configuration

        configuration = deepcopy(configuration)

        if runtime_configuration:
            configuration.kwargs.update(runtime_configuration)

        success_kwargs = self.get_success_kwargs(configuration)
        runtime_kwargs = {
            key: configuration.kwargs.get(key, self.default_kwarg_values.get(key))
            for key in self.runtime_keys
        }
        runtime_kwargs.update(success_kwargs)

        runtime_kwargs["result_format"] = parse_result_format(
            runtime_kwargs["result_format"]
        )

        return runtime_kwargs

    def validate_configuration(self, configuration: Optional[ExpectationConfiguration]):
        if configuration is None:
            configuration = self.configuration
        try:
            assert configuration.expectation_type == self.expectation_type, (
                "expectation configuration type does not match " "expectation type"
            )
        except AssertionError as e:
            raise InvalidExpectationConfigurationError(str(e))
        return True

    def validate(
        self,
        validator: "Validator",
        configuration: Optional[ExpectationConfiguration] = None,
        evaluation_parameters=None,
        interactive_evaluation=True,
        data_context=None,
        runtime_configuration=None,
    ):
        if configuration is None:
            configuration = self.configuration

        configuration.build_evaluation_parameters(
            evaluation_parameters, interactive_evaluation, data_context
        )
        evr = validator.graph_validate(
            configurations=[configuration], runtime_configuration=runtime_configuration,
        )[0]

        return evr

    @property
    def configuration(self):
        if self._configuration is None:
            raise InvalidExpectationConfigurationError(
                "cannot access configuration: expectation has not yet been configured"
            )
        return self._configuration

    @classmethod
    def build_configuration(cls, *args, **kwargs):
        # Combine all arguments into a single new "all_args" dictionary to name positional parameters
        all_args = dict(zip(cls.validation_kwargs, args))
        all_args.update(kwargs)

        # Unpack display parameters; remove them from all_args if appropriate
        if "include_config" in kwargs:
            include_config = kwargs["include_config"]
            del all_args["include_config"]
        else:
            include_config = cls.default_expectation_args["include_config"]

        if "catch_exceptions" in kwargs:
            catch_exceptions = kwargs["catch_exceptions"]
            del all_args["catch_exceptions"]
        else:
            catch_exceptions = cls.default_expectation_args["catch_exceptions"]

        if "result_format" in kwargs:
            result_format = kwargs["result_format"]
        else:
            result_format = cls.default_expectation_args["result_format"]

        # Extract the meta object for use as a top-level expectation_config holder
        if "meta" in kwargs:
            meta = kwargs["meta"]
            del all_args["meta"]
        else:
            meta = None

        # Construct the expectation_config object
        return ExpectationConfiguration(
            expectation_type=cls.expectation_type,
            kwargs=convert_to_json_serializable(deepcopy(all_args)),
            meta=meta,
        )


class TableExpectation(Expectation, ABC):
    domain_keys = (
        "batch_id",
        "table",
        "row_condition",
        "condition_parser",
    )
    metric_dependencies = tuple()

    def get_validation_dependencies(
        self,
        configuration: Optional[ExpectationConfiguration] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        runtime_configuration: Optional[dict] = None,
    ):
        dependencies = super().get_validation_dependencies(
            configuration, execution_engine, runtime_configuration
        )
        for metric_name in self.metric_dependencies:
            metric_kwargs = get_metric_kwargs(
                metric_name=metric_name,
                configuration=configuration,
                runtime_configuration=runtime_configuration,
            )
            dependencies["metrics"][metric_name] = MetricConfiguration(
                metric_name=metric_name,
                metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
                metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
            )

        return dependencies

    def validate_metric_value_between_configuration(
        self, configuration: Optional[ExpectationConfiguration]
    ):
        # Validating that Minimum and Maximum values are of the proper format and type
        min_val = None
        max_val = None

        if "min_value" in configuration.kwargs:
            min_val = configuration.kwargs["min_value"]

        if "max_value" in configuration.kwargs:
            max_val = configuration.kwargs["max_value"]

        try:
            # Ensuring Proper interval has been provided
            assert min_val is None or isinstance(
                min_val, (float, int)
            ), "Provided min threshold must be a number"
            assert max_val is None or isinstance(
                max_val, (float, int)
            ), "Provided max threshold must be a number"

        except AssertionError as e:
            raise InvalidExpectationConfigurationError(str(e))

        if min_val is not None and max_val is not None and min_val > max_val:
            raise InvalidExpectationConfigurationError(
                "Minimum Threshold cannot be larger than Maximum Threshold"
            )

        return True

    def _validate_metric_value_between(
        self,
        metric_name,
        configuration: ExpectationConfiguration,
        metrics: Dict,
        runtime_configuration: dict = None,
        execution_engine: ExecutionEngine = None,
    ):
        metric_value = metrics.get(metric_name)

        # Obtaining components needed for validation
        min_value = self.get_success_kwargs(configuration).get("min_value")
        strict_min = self.get_success_kwargs(configuration).get("strict_min")
        max_value = self.get_success_kwargs(configuration).get("max_value")
        strict_max = self.get_success_kwargs(configuration).get("strict_max")

        if metric_value is None:
            return {"success": False, "result": {"observed_value": metric_value}}

        # Checking if mean lies between thresholds
        if min_value is not None:
            if strict_min:
                above_min = metric_value > min_value
            else:
                above_min = metric_value >= min_value
        else:
            above_min = True

        if max_value is not None:
            if strict_max:
                below_max = metric_value < max_value
            else:
                below_max = metric_value <= max_value
        else:
            below_max = True

        success = above_min and below_max

        return {"success": success, "result": {"observed_value": metric_value}}


class ColumnExpectation(TableExpectation, ABC):
    domain_keys = ("batch_id", "table", "column", "row_condition", "condition_parser")

    def validate_configuration(self, configuration: Optional[ExpectationConfiguration]):
        # Ensuring basic configuration parameters are properly set
        try:
            assert (
                "column" in configuration.kwargs
            ), "'column' parameter is required for column expectations"
        except AssertionError as e:
            raise InvalidExpectationConfigurationError(str(e))
        return True


class ColumnMapExpectation(TableExpectation, ABC):
    map_metric = None
    domain_keys = ("batch_id", "table", "column", "row_condition", "condition_parser")
    success_keys = ("mostly",)
    default_kwarg_values = {
        "row_condition": None,
        "condition_parser": None,  # we expect this to be explicitly set whenever a row_condition is passed
        "mostly": 1,
        "result_format": "BASIC",
        "include_config": True,
        "catch_exceptions": True,
    }

    def validate_configuration(self, configuration: Optional[ExpectationConfiguration]):
        if not super().validate_configuration(configuration):
            return False
        try:
            assert (
                "column" in configuration.kwargs
            ), "'column' parameter is required for column map expectations"
            if "mostly" in configuration.kwargs:
                mostly = configuration.kwargs["mostly"]
                assert isinstance(
                    mostly, (int, float)
                ), "'mostly' parameter must be an integer or float"
                assert 0 <= mostly <= 1, "'mostly' parameter must be between 0 and 1"
        except AssertionError as e:
            raise InvalidExpectationConfigurationError(str(e))
        return True

    def get_validation_dependencies(
        self,
        configuration: Optional[ExpectationConfiguration] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        runtime_configuration: Optional[dict] = None,
    ):
        dependencies = super().get_validation_dependencies(
            configuration, execution_engine, runtime_configuration
        )
        assert isinstance(
            self.map_metric, str
        ), "ColumnMapExpectation must override get_validation_dependencies or declare exactly one map_metric"
        assert (
            self.metric_dependencies == tuple()
        ), "ColumnMapExpectation must be configured using map_metric, and cannot have metric_dependencies declared."
        # convenient name for updates
        metric_dependencies = dependencies["metrics"]
        metric_kwargs = get_metric_kwargs(
            metric_name="column_values.nonnull.unexpected_count",
            configuration=configuration,
            runtime_configuration=runtime_configuration,
        )
        metric_dependencies[
            "column_values.nonnull.unexpected_count"
        ] = MetricConfiguration(
            "column_values.nonnull.unexpected_count",
            metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
            metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
        )
        metric_kwargs = get_metric_kwargs(
            metric_name=self.map_metric + ".unexpected_count",
            configuration=configuration,
            runtime_configuration=runtime_configuration,
        )
        metric_dependencies[
            self.map_metric + ".unexpected_count"
        ] = MetricConfiguration(
            self.map_metric + ".unexpected_count",
            metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
            metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
        )

        result_format_str = dependencies["result_format"].get("result_format")
        metric_kwargs = get_metric_kwargs(
            metric_name="table.row_count",
            configuration=configuration,
            runtime_configuration=runtime_configuration,
        )
        metric_dependencies["table.row_count"] = MetricConfiguration(
            metric_name="table.row_count",
            metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
            metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
        )
        if result_format_str == "BOOLEAN_ONLY":
            return dependencies

        metric_kwargs = get_metric_kwargs(
            self.map_metric + ".unexpected_values",
            configuration=configuration,
            runtime_configuration=runtime_configuration,
        )
        metric_dependencies[
            self.map_metric + ".unexpected_values"
        ] = MetricConfiguration(
            metric_name=self.map_metric + ".unexpected_values",
            metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
            metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
        )

        if result_format_str in ["BASIC", "SUMMARY"]:
            return dependencies

        metric_kwargs = get_metric_kwargs(
            self.map_metric + ".unexpected_rows",
            configuration=configuration,
            runtime_configuration=runtime_configuration,
        )
        metric_dependencies[self.map_metric + ".unexpected_rows"] = MetricConfiguration(
            metric_name=self.map_metric + ".unexpected_rows",
            metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
            metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
        )

        if isinstance(execution_engine, PandasExecutionEngine):
            metric_kwargs = get_metric_kwargs(
                self.map_metric + ".unexpected_index_list",
                configuration=configuration,
                runtime_configuration=runtime_configuration,
            )
            metric_dependencies[
                self.map_metric + ".unexpected_index_list"
            ] = MetricConfiguration(
                metric_name=self.map_metric + ".unexpected_index_list",
                metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
                metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
            )

        return dependencies

    def _validate(
        self,
        configuration: ExpectationConfiguration,
        metrics: Dict,
        runtime_configuration: dict = None,
        execution_engine: ExecutionEngine = None,
    ):
        if runtime_configuration:
            result_format = runtime_configuration.get(
                "result_format",
                configuration.kwargs.get(
                    "result_format", self.default_kwarg_values.get("result_format")
                ),
            )
        else:
            result_format = configuration.kwargs.get(
                "result_format", self.default_kwarg_values.get("result_format")
            )
        mostly = self.get_success_kwargs().get(
            "mostly", self.default_kwarg_values.get("mostly")
        )
        total_count = metrics.get("table.row_count")
        null_count = metrics.get("column_values.nonnull.unexpected_count")
        unexpected_count = metrics.get(self.map_metric + ".unexpected_count")

        success = None
        if total_count is None or null_count is None:
            # Vacuously true
            success = True
        elif (total_count - null_count) != 0:
            success_ratio = (total_count - unexpected_count - null_count) / (
                total_count - null_count
            )
            success = success_ratio >= mostly
        elif total_count == 0 or (total_count - null_count) == 0:
            success = True

        try:
            nonnull_count = metrics.get("table.row_count") - metrics.get(
                "column_values.nonnull.unexpected_count"
            )
        except TypeError:
            nonnull_count = None

        return _format_map_output(
            result_format=parse_result_format(result_format),
            success=success,
            element_count=metrics.get("table.row_count"),
            nonnull_count=nonnull_count,
            unexpected_count=metrics.get(self.map_metric + ".unexpected_count"),
            unexpected_list=metrics.get(self.map_metric + ".unexpected_values"),
            unexpected_index_list=metrics.get(
                self.map_metric + ".unexpected_index_list"
            ),
        )


class ColumnPairMapExpectation(TableExpectation, ABC):
    map_metric = None
    domain_keys = (
        "batch_id",
        "table",
        "column_A",
        "column_B",
        "row_condition",
        "condition_parser",
    )
    success_keys = ("mostly",)
    default_kwarg_values = {
        "row_condition": None,
        "condition_parser": None,  # we expect this to be explicitly set whenever a row_condition is passed
        "mostly": 1,
        "result_format": "BASIC",
        "include_config": True,
        "catch_exceptions": True,
    }

    def validate_configuration(self, configuration: Optional[ExpectationConfiguration]):
        if not super().validate_configuration(configuration):
            return False
        try:
            assert (
                "column_A" in configuration.kwargs
            ), "'column_A' parameter is required for column pair map expectations"
            assert (
                "column_B" in configuration.kwargs
            ), "'column_B' parameter is required for column pair map expectations"
            if "mostly" in configuration.kwargs:
                mostly = configuration.kwargs["mostly"]
                assert isinstance(
                    mostly, (int, float)
                ), "'mostly' parameter must be an integer or float"
                assert 0 <= mostly <= 1, "'mostly' parameter must be between 0 and 1"
        except AssertionError as e:
            raise InvalidExpectationConfigurationError(str(e))
        return True

    def get_validation_dependencies(
        self,
        configuration: Optional[ExpectationConfiguration] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        runtime_configuration: Optional[dict] = None,
    ):
        dependencies = super().get_validation_dependencies(
            configuration, execution_engine, runtime_configuration
        )
        assert isinstance(
            self.map_metric, str
        ), "ColumnPairMapExpectation must override get_validation_dependencies or declare exactly one map_metric"
        assert (
            self.metric_dependencies == tuple()
        ), "ColumnPairMapExpectation must be configured using map_metric, and cannot have metric_dependencies declared."
        # convenient name for updates
        metric_dependencies = dependencies["metrics"]
        metric_kwargs = get_metric_kwargs(
            metric_name="column_values.nonnull.unexpected_count",
            configuration=configuration,
            runtime_configuration=runtime_configuration,
        )
        metric_dependencies[
            "column_values.nonnull.unexpected_count"
        ] = MetricConfiguration(
            "column_values.nonnull.unexpected_count",
            metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
            metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
        )
        metric_kwargs = get_metric_kwargs(
            metric_name=self.map_metric + ".unexpected_count",
            configuration=configuration,
            runtime_configuration=runtime_configuration,
        )
        metric_dependencies[
            self.map_metric + ".unexpected_count"
        ] = MetricConfiguration(
            self.map_metric + ".unexpected_count",
            metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
            metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
        )

        result_format_str = dependencies["result_format"].get("result_format")
        if result_format_str == "BOOLEAN_ONLY":
            return dependencies

        metric_kwargs = get_metric_kwargs(
            metric_name="table.row_count",
            configuration=configuration,
            runtime_configuration=runtime_configuration,
        )
        metric_dependencies["table.row_count"] = MetricConfiguration(
            metric_name="table.row_count",
            metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
            metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
        )

        metric_kwargs = get_metric_kwargs(
            self.map_metric + ".unexpected_values",
            configuration=configuration,
            runtime_configuration=runtime_configuration,
        )
        metric_dependencies[
            self.map_metric + ".unexpected_values"
        ] = MetricConfiguration(
            metric_name=self.map_metric + ".unexpected_values",
            metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
            metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
        )

        if result_format_str in ["BASIC", "SUMMARY"]:
            return dependencies

        metric_kwargs = get_metric_kwargs(
            self.map_metric + ".unexpected_rows",
            configuration=configuration,
            runtime_configuration=runtime_configuration,
        )
        metric_dependencies[self.map_metric + ".unexpected_rows"] = MetricConfiguration(
            metric_name=self.map_metric + ".unexpected_rows",
            metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
            metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
        )

        if isinstance(execution_engine, PandasExecutionEngine):
            metric_kwargs = get_metric_kwargs(
                self.map_metric + ".unexpected_index_list",
                configuration=configuration,
                runtime_configuration=runtime_configuration,
            )
            metric_dependencies[
                self.map_metric + ".unexpected_index_list"
            ] = MetricConfiguration(
                metric_name=self.map_metric + ".unexpected_index_list",
                metric_domain_kwargs=metric_kwargs["metric_domain_kwargs"],
                metric_value_kwargs=metric_kwargs["metric_value_kwargs"],
            )

        return dependencies

    def _validate(
        self,
        configuration: ExpectationConfiguration,
        metrics: Dict,
        runtime_configuration: dict = None,
        execution_engine: ExecutionEngine = None,
    ):
        if runtime_configuration:
            result_format = runtime_configuration.get(
                "result_format",
                configuration.kwargs.get(
                    "result_format", self.default_kwarg_values.get("result_format")
                ),
            )
        else:
            result_format = configuration.kwargs.get(
                "result_format", self.default_kwarg_values.get("result_format")
            )
        mostly = self.get_success_kwargs().get(
            "mostly", self.default_kwarg_values.get("mostly")
        )
        total_count = metrics.get("table.row_count")
        null_count = metrics.get("column_values.nonnull.unexpected_count")
        unexpected_count = metrics.get(self.map_metric + ".unexpected_count")

        success = None
        if total_count is None or null_count is None:
            # Vacuously true
            success = True
        elif (total_count - null_count) != 0:
            success_ratio = (total_count - unexpected_count - null_count) / (
                total_count - null_count
            )
            success = success_ratio >= mostly
        elif total_count == 0 or (total_count - null_count) == 0:
            success = True

        try:
            nonnull_count = metrics.get("table.row_count") - metrics.get(
                "column_values.nonnull.unexpected_count"
            )
        except TypeError:
            nonnull_count = None

        return _format_map_output(
            result_format=parse_result_format(result_format),
            success=success,
            element_count=metrics.get("table.row_count"),
            nonnull_count=nonnull_count,
            unexpected_count=metrics.get(self.map_metric + ".unexpected_count"),
            unexpected_list=metrics.get(self.map_metric + ".unexpected_values"),
            unexpected_index_list=metrics.get(
                self.map_metric + ".unexpected_index_list"
            ),
        )


def _format_map_output(
    result_format,
    success,
    element_count,
    nonnull_count,
    unexpected_count,
    unexpected_list,
    unexpected_index_list,
):
    """Helper function to construct expectation result objects for map_expectations (such as column_map_expectation
    and file_lines_map_expectation).

    Expectations support four result_formats: BOOLEAN_ONLY, BASIC, SUMMARY, and COMPLETE.
    In each case, the object returned has a different set of populated fields.
    See :ref:`result_format` for more information.

    This function handles the logic for mapping those fields for column_map_expectations.
    """
    # NB: unexpected_count parameter is explicit some implementing classes may limit the length of unexpected_list
    # Incrementally add to result and return when all values for the specified level are present
    return_obj = {"success": success}

    if result_format["result_format"] == "BOOLEAN_ONLY":
        return return_obj

    skip_missing = False

    if nonnull_count is None:
        missing_count = None
        skip_missing: bool = True
    else:
        missing_count = element_count - nonnull_count

    if element_count > 0:
        unexpected_percent = unexpected_count / element_count * 100

        if not skip_missing:
            missing_percent = missing_count / element_count * 100
            if nonnull_count > 0:
                unexpected_percent_nonmissing = unexpected_count / nonnull_count * 100
            else:
                unexpected_percent_nonmissing = None

    else:
        missing_percent = None
        unexpected_percent = None
        unexpected_percent_nonmissing = None

    return_obj["result"] = {
        "element_count": element_count,
        "unexpected_count": unexpected_count,
        "unexpected_percent": unexpected_percent,
        "partial_unexpected_list": unexpected_list[
            : result_format["partial_unexpected_count"]
        ],
    }

    if not skip_missing:
        return_obj["result"]["missing_count"] = missing_count
        return_obj["result"]["missing_percent"] = missing_percent
        return_obj["result"][
            "unexpected_percent_nonmissing"
        ] = unexpected_percent_nonmissing

    if result_format["result_format"] == "BASIC":
        return return_obj

    # Try to return the most common values, if possible.
    if 0 < result_format.get("partial_unexpected_count"):
        try:
            partial_unexpected_counts = [
                {"value": key, "count": value}
                for key, value in sorted(
                    Counter(unexpected_list).most_common(
                        result_format["partial_unexpected_count"]
                    ),
                    key=lambda x: (-x[1], x[0]),
                )
            ]
        except TypeError:
            partial_unexpected_counts = [
                "partial_exception_counts requires a hashable type"
            ]
        finally:
            return_obj["result"].update(
                {
                    "partial_unexpected_index_list": unexpected_index_list[
                        : result_format["partial_unexpected_count"]
                    ]
                    if unexpected_index_list is not None
                    else None,
                    "partial_unexpected_counts": partial_unexpected_counts,
                }
            )

    if result_format["result_format"] == "SUMMARY":
        return return_obj

    return_obj["result"].update(
        {
            "unexpected_list": unexpected_list,
            "unexpected_index_list": unexpected_index_list,
        }
    )

    if result_format["result_format"] == "COMPLETE":
        return return_obj

    raise ValueError("Unknown result_format {}.".format(result_format["result_format"]))
