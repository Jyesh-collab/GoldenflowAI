import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:bagisto_flutter/core/recently_viewed/recently_viewed_products_service.dart';
import 'package:bagisto_flutter/features/category/data/models/product_model.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    RecentlyViewedProductsService.changes.value = 0;
  });

  ProductModel buildProduct() {
    return const ProductModel(
      id: 'gid://bagisto/Product/101',
      numericId: 101,
      sku: 'sku-101',
      type: 'simple',
      name: 'Test Product',
      urlKey: 'test-product',
      price: 99,
      formattedPrice: '\$99',
      isSaleable: true,
    );
  }

  test('tracks products when the preference has never been written', () async {
    await RecentlyViewedProductsService.trackProduct(buildProduct());

    final recentProducts =
        await RecentlyViewedProductsService.getRecentProducts();

    expect(recentProducts, hasLength(1));
    expect(recentProducts.first.numericId, 101);
    expect(recentProducts.first.urlKey, 'test-product');
    expect(RecentlyViewedProductsService.changes.value, 1);
  });

  test('does not track products when recently viewed is disabled', () async {
    SharedPreferences.setMockInitialValues({
      'settings_track_recently_viewed': false,
    });

    await RecentlyViewedProductsService.trackProduct(buildProduct());

    final recentProducts =
        await RecentlyViewedProductsService.getRecentProducts();

    expect(recentProducts, isEmpty);
    expect(RecentlyViewedProductsService.changes.value, 0);
  });
}
