---
name: maestro-mobile-testing
description: "Generate high-quality, industry-standard Maestro mobile test cases for Android & iOS (Maestro UI test generator + best practice enforcement)."
author: your-name-or-org-name
tags:
  - maestro
  - mobile-testing
  - qa
  - automation
  - android
  - ios
  - yaml
  - best-practices
---

# Maestro Mobile Testing Skill

## Overview

This skill provides AI agents with the capability to **generate robust, maintainable Maestro test flows (YAML)** for mobile applications (Android & iOS).  
It enforces testing best practices, reusable flows, validations, and CI readiness.

Use this skill to create:

* Smoke tests
* Regression test suites
* UI validation + navigation flows
* Input validation tests
* Edge-case + error-scenario tests
* Cross-platform Maestro flows

---

## Usage Instructions

When invoked, the AI agent should:

1. Ask for:
   * Target app (Android, iOS, or both)
   * Screen/feature description
   * Test types required (smoke, regression, negative, etc.)
   * Any specific flows (login, onboarding, forms, etc.)

2. Return:
   * Clean **Maestro YAML test code**
   * Well-commented structure
   * Metadata and tags
   * Suggested reusable subflows
   * Optional CI integration tips

---

## Quality Rules

Generated test cases MUST:

* Be valid Maestro YAML syntax
* Use `waitFor`, not hard sleeps
* Include assertions (`assertVisible`, `assertNotVisible`)
* Use accessibility IDs or test tags
* Have clear structure with comments
* Cover positive, negative, & edge cases
* Be modular and scalable

---

## Example Prompt

> “Generate Maestro tests for the login screen:  
> - Valid login  
> - Invalid password  
> - Empty fields validation  
> Target platform: Android & iOS”

Expected Output:

```yaml
appId: com.example.app
---
# Smoke test — Login
- launchApp
- clearState

# Login screen visible
- assertVisible: "Login Screen"

# Empty Fields
- tapOn:
    id: "login_button"
- assertVisible: "Error: Email required"

# Invalid Password
- tapOn: { id: "email_input" }
- inputText: "user@example.com"
- tapOn: { id: "password_input" }
- inputText: "wrongpass"
- tapOn: { id: "login_button" }
- assertVisible: "Error: Invalid credentials"

# Valid Login
- tapOn: { id: "email_input" }
- inputText: "user@example.com"
- tapOn: { id: "password_input" }
- inputText: "CorrectPass123"
- tapOn: { id: "login_button" }
- waitFor:
    visible: "Home Screen"
    timeout: 5000
- assertVisible: "Welcome"

---

# Full App Test Flow (Android + iOS)

Use this blueprint to produce a **single, end-to-end Maestro suite**. Keep steps modular so they can be reused as subflows.

## 🔐 Authentication Flow
1. Launch App
2. Verify Splash Screen
3. Navigate to Login Screen
4. Test Login: valid credentials, invalid credentials
5. Navigate to Signup Screen
6. Test Signup: valid data, existing user validation, field validations

## 🏠 Home & Navigation Flow
1. Verify Home Page Load
2. Validate Banner Visibility
3. Navigate to Categories
4. Navigate to Catalog (Product Listing)

## 📂 Category & Catalog Flow
1. Open Categories Page
2. Select Category
3. Verify Product Listing
4. Apply Filters (if available)
5. Apply Sorting

## 📦 Product Page Flow
### Simple Product
1. Open Product Detail Page
2. Verify: Product Image, Name, Price, Description
3. Add to Cart

### Configurable Product
1. Open Product Detail Page
2. Select Options (size, color, etc.)
3. Validate price/image change
4. Add to Cart

## 🛒 Cart Flow
1. Open Cart Page
2. Verify added products
3. Update quantity
4. Remove product
5. Proceed to Checkout

## 💳 Checkout Flow
### Guest Checkout
1. Continue as Guest
2. Enter Shipping Address
3. Select Shipping Method
4. Select Payment Method
5. Place Order

### Customer Checkout
1. Login User
2. Use Saved Address / Add New
3. Select Shipping Method
4. Select Payment Method
5. Place Order

## 📄 Order Flow
1. Navigate to My Orders
2. Open Order Details
3. Verify: Product details, Price breakdown, Order status

---

# Test Case Matrix (IDs stay stable across platforms)

## 🔐 Authentication
- TC_AUTH_01: Login with valid credentials
- TC_AUTH_02: Login with invalid credentials
- TC_AUTH_03: Signup with new user
- TC_AUTH_04: Signup with existing email
- TC_AUTH_05: Validate mandatory fields

## 🏠 Home
- TC_HOME_01: Home page loads successfully
- TC_HOME_02: Banner visible and clickable
- TC_HOME_03: Navigation to categories

## 📂 Category & Catalog
- TC_CAT_01: Category page loads
- TC_CAT_02: Category selection loads products
- TC_CAT_03: Filter works correctly
- TC_CAT_04: Sorting works correctly

## 📦 Product
### Simple Product
- TC_PROD_01: Product details visible
- TC_PROD_02: Add to cart works

### Configurable Product
- TC_PROD_03: Options selection required
- TC_PROD_04: Variant updates correctly
- TC_PROD_05: Add to cart after selection

## 🛒 Cart
- TC_CART_01: Cart loads correctly
- TC_CART_02: Update quantity
- TC_CART_03: Remove item
- TC_CART_04: Proceed to checkout

## 💳 Checkout
### Guest
- TC_CHECK_01: Guest checkout flow
- TC_CHECK_02: Address validation
- TC_CHECK_03: Payment selection
- TC_CHECK_04: Order placement

### Customer
- TC_CHECK_05: Logged-in checkout
- TC_CHECK_06: Saved address selection
- TC_CHECK_07: Order placement

## 📄 Order
- TC_ORDER_01: Order listing loads
- TC_ORDER_02: Order details visible
- TC_ORDER_03: Correct order data

---

# Reusable Keys File

Store cross-platform selectors in `maestro/keys.yaml`:

```yaml
# Authentication
login_button: "loginBtn"
email_field: "emailInput"
password_field: "passwordInput"
signup_button: "signupBtn"

# Home
home_banner: "homeBanner"
categories_tab: "categoriesTab"

# Category & Catalog
category_item: "categoryItem"
product_item: "productItem"
filter_button: "filterBtn"
sort_button: "sortBtn"

# Product Page
product_name: "productName"
product_price: "productPrice"
add_to_cart_button: "addToCartBtn"
config_option: "configOption"

# Cart
cart_icon: "cartIcon"
cart_item: "cartItem"
checkout_button: "checkoutBtn"

# Checkout
guest_checkout: "guestCheckoutBtn"
address_field: "addressInput"
payment_method: "paymentMethod"
place_order_button: "placeOrderBtn"

# Order
my_orders: "myOrdersTab"
order_item: "orderItem"
order_details: "orderDetails"
```

Best practices:
- Prefer dynamic waits (`waitFor` / `assertVisible`) over fixed sleeps.
- Reuse selectors from `keys.yaml` to keep Android/iOS parity.
- Parameterize environment configs (staging/live) via Maestro `env` or injected variables.
- Keep flows modular: one YAML per feature plus an orchestrating e2e flow.
- Tag tests by feature (auth, catalog, checkout) for CI targeting.

Execution note: Android emulator is enabled; when running end-to-end suites use Maestro MCP with the active emulator or device (`maestro test <flow.yaml> --device <udid>`). If you need iOS simulator, update the UDID in scripts like `test_maestro_mcp.sh`.
