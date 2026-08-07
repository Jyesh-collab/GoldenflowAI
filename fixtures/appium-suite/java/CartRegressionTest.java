package com.acme.shop.tests;

import io.appium.java_client.AppiumBy;
import io.appium.java_client.android.AndroidDriver;
import org.testng.Assert;
import org.testng.annotations.Test;

import com.acme.shop.pages.CartScreen;
import com.acme.shop.pages.HomeScreen;
import com.acme.shop.pages.ProductDetailScreen;

/**
 * TestNG cart regression. Exercises the heuristic Java parser.
 *
 * The comment below deliberately names a Page Object that this test never uses -
 * the parser strips comments before matching, so DeliveryAddressScreen must not
 * show up as coverage.
 *
 * TODO: extend this to DeliveryAddressScreen once GF-388 lands.
 */
public class CartRegressionTest {

    private AndroidDriver driver;

    @Test(groups = {"regression", "cart"})
    public void addToCartUpdatesBadgeCount() {
        HomeScreen home = new HomeScreen(driver);
        home.openSearch();

        ProductDetailScreen pdp = new ProductDetailScreen(driver);
        pdp.addToCart();

        CartScreen cart = new CartScreen(driver);
        Assert.assertEquals(cart.itemCount(), 1);
        Assert.assertTrue(cart.subtotal().contains("."));
    }

    @Test(groups = {"regression"})
    public void emptyCartShowsEmptyState() {
        CartScreen cart = new CartScreen(driver);
        Assert.assertEquals(cart.itemCount(), 0);
    }
}
