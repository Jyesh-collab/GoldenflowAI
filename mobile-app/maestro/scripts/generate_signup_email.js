var prefix = typeof EMAIL_PREFIX !== "undefined" ? EMAIL_PREFIX : "qa.signup";
var timestamp = String(Date.now());

output.UNIQUE_SIGNUP_EMAIL = prefix + "+" + timestamp + "@example.com";
