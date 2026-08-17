import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../features/category/data/models/product_model.dart';
import '../../features/home/data/models/home_models.dart';

class RecentlyViewedProductsService {
  RecentlyViewedProductsService._();

  static const _prefsKey = 'recently_viewed_products';
  static const _maxItems = 12;
  static const _trackKey = 'settings_track_recently_viewed';

  static final ValueNotifier<int> changes = ValueNotifier<int>(0);

  static bool _isTrackingEnabled(SharedPreferences prefs) {
    return prefs.getBool(_trackKey) ?? true;
  }

  static Future<List<HomeProduct>> getRecentProducts() async {
    final prefs = await SharedPreferences.getInstance();
    if (!_isTrackingEnabled(prefs)) return const <HomeProduct>[];
    final raw = prefs.getString(_prefsKey);
    if (raw == null || raw.isEmpty) return const <HomeProduct>[];

    try {
      final decoded = jsonDecode(raw);
      if (decoded is! List) return const <HomeProduct>[];

      return decoded
          .whereType<Map<String, dynamic>>()
          .map(HomeProduct.fromJson)
          .where(
            (product) => product.name.isNotEmpty && product.urlKey.isNotEmpty,
          )
          .toList();
    } catch (_) {
      return const <HomeProduct>[];
    }
  }

  static Future<void> trackProduct(ProductModel product) async {
    final prefs = await SharedPreferences.getInstance();
    if (!_isTrackingEnabled(prefs)) return;

    final numericId =
        product.numericId ?? int.tryParse(product.id.split('/').last);
    final name = product.name?.trim() ?? '';
    final urlKey = product.urlKey?.trim() ?? '';

    if (numericId == null || numericId <= 0 || name.isEmpty || urlKey.isEmpty) {
      return;
    }

    final current = await getRecentProducts();
    final recentProduct = HomeProduct(
      id: product.id,
      numericId: numericId,
      sku: product.sku ?? '',
      type: product.type ?? 'simple',
      name: name,
      urlKey: urlKey,
      baseImageUrl: product.baseImageUrl,
      price: product.price ?? 0,
      minimumPrice: product.minimumPrice,
      specialPrice: product.specialPrice,
      formattedPrice: product.formattedPrice,
      formattedMinimumPrice: product.formattedMinimumPrice,
      formattedSpecialPrice: product.formattedSpecialPrice,
      isSaleable: product.isSaleable ?? true,
      averageRating: product.averageRating,
      reviewCount: product.reviewCount,
    );

    final updated = <HomeProduct>[
      recentProduct,
      ...current.where(
        (item) =>
            item.numericId != recentProduct.numericId &&
            item.urlKey != recentProduct.urlKey,
      ),
    ].take(_maxItems).toList();

    await prefs.setString(
      _prefsKey,
      jsonEncode(updated.map((product) => product.toJson()).toList()),
    );
    changes.value++;
  }

  static Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_prefsKey);
    changes.value++;
  }
}
