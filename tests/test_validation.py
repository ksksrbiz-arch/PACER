"""Test suite for validation system."""

import pytest

from pacer.validation import ValidationError, ValidationRule, Validator


class TestValidationRule:
    """Test cases for ValidationRule."""

    def test_valid_rule(self):
        """Test rule with valid input."""
        rule = ValidationRule(
            name="positive",
            validator=lambda x: x > 0,
            error_message="Value must be positive"
        )

        # Should not raise
        rule.validate(5)

    def test_invalid_rule(self):
        """Test rule with invalid input."""
        rule = ValidationRule(
            name="positive",
            validator=lambda x: x > 0,
            error_message="Value must be positive"
        )

        with pytest.raises(ValidationError) as exc_info:
            rule.validate(-5)

        assert "Value must be positive" in str(exc_info.value)


class TestValidator:
    """Test cases for Validator."""

    def test_add_rule(self):
        """Test adding validation rule."""
        validator = Validator()
        rule = Validator.not_none()

        validator.add_rule("field1", rule)
        assert "field1" in validator.rules

    def test_validate_field_success(self):
        """Test successful field validation."""
        validator = Validator()
        validator.add_rule("name", Validator.not_empty())

        # Should not raise
        validator.validate_field("name", "John")

    def test_validate_field_failure(self):
        """Test failed field validation."""
        validator = Validator()
        validator.add_rule("name", Validator.not_empty())

        with pytest.raises(ValidationError) as exc_info:
            validator.validate_field("name", "")

        assert exc_info.value.field == "name"

    def test_validate_dict(self):
        """Test dictionary validation."""
        validator = Validator()
        validator.add_rule("name", Validator.not_empty())
        validator.add_rule("age", Validator.in_range(0, 120))

        # Valid data
        validator.validate_dict({"name": "John", "age": 30})

        # Invalid data
        with pytest.raises(ValidationError):
            validator.validate_dict({"name": "", "age": 30})

    def test_not_none_rule(self):
        """Test not_none validation rule."""
        rule = Validator.not_none()

        rule.validate("value")
        rule.validate(0)
        rule.validate(False)

        with pytest.raises(ValidationError):
            rule.validate(None)

    def test_not_empty_rule(self):
        """Test not_empty validation rule."""
        rule = Validator.not_empty()

        rule.validate("value")
        rule.validate([1, 2, 3])
        rule.validate({"key": "value"})

        with pytest.raises(ValidationError):
            rule.validate("")

        with pytest.raises(ValidationError):
            rule.validate([])

    def test_min_length_rule(self):
        """Test min_length validation rule."""
        rule = Validator.min_length(5)

        rule.validate("hello")
        rule.validate("hello world")

        with pytest.raises(ValidationError):
            rule.validate("hi")

    def test_max_length_rule(self):
        """Test max_length validation rule."""
        rule = Validator.max_length(10)

        rule.validate("hello")
        rule.validate("hi")

        with pytest.raises(ValidationError):
            rule.validate("this is too long")

    def test_in_range_rule(self):
        """Test in_range validation rule."""
        rule = Validator.in_range(0, 100)

        rule.validate(0)
        rule.validate(50)
        rule.validate(100)

        with pytest.raises(ValidationError):
            rule.validate(-1)

        with pytest.raises(ValidationError):
            rule.validate(101)

    def test_matches_pattern_rule(self):
        """Test matches_pattern validation rule."""
        rule = Validator.matches_pattern(r"^\d{3}-\d{4}$")

        rule.validate("123-4567")

        with pytest.raises(ValidationError):
            rule.validate("invalid")

    def test_is_type_rule(self):
        """Test is_type validation rule."""
        rule = Validator.is_type(int)

        rule.validate(42)

        with pytest.raises(ValidationError):
            rule.validate("42")

    def test_multiple_rules_per_field(self):
        """Test applying multiple rules to one field."""
        validator = Validator()
        validator.add_rule("password", Validator.not_empty())
        validator.add_rule("password", Validator.min_length(8))

        # Valid password
        validator.validate_field("password", "secure123")

        # Too short
        with pytest.raises(ValidationError):
            validator.validate_field("password", "short")

        # Empty
        with pytest.raises(ValidationError):
            validator.validate_field("password", "")
