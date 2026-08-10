import 'package:flutter/material.dart';
import '../../../../core/theme/app_theme.dart';
import '../../../../l10n/app_localizations.dart';
import '../../../category/data/models/product_model.dart';

/// Product info: title, price row, rating badge + review count, stock chip
/// Figma: Frame 1984079200 – below the image carousel
class ProductInfoSection extends StatelessWidget {
  final ProductModel product;
  final ProductVariant? selectedVariant;

  const ProductInfoSection({
    super.key,
    required this.product,
    this.selectedVariant,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Product Name ──
          Text(
            product.name ?? l10n.productDefaultName,
            style: AppTextStyles.text4(context).copyWith(
              color: isDark ? AppColors.neutral300 : AppColors.neutral800,
            ),
          ),

          // ── Short Description (configurable products only) ──
          if (product.isConfigurable &&
              product.shortDescription != null &&
              product.shortDescription!.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                _stripHtml(product.shortDescription!),
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: AppTextStyles.bodyText(context).copyWith(
                  color: isDark ? AppColors.neutral400 : AppColors.neutral600,
                ),
              ),
            ),

          const SizedBox(height: 12),

          // ── Price Row ──
          _buildPriceRow(context),

          const SizedBox(height: 12),

          // ── Rating + Stock Row ──
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [_buildRatingGroup(context), _buildStockChip(context)],
          ),
        ],
      ),
    );
  }

  String _stripHtml(String html) {
    return html
        .replaceAll(RegExp(r'<[^>]*>'), '')
        .replaceAll('&nbsp;', ' ')
        .replaceAll('&amp;', '&')
        .replaceAll('&lt;', '<')
        .replaceAll('&gt;', '>')
        .replaceAll('&quot;', '"')
        .trim();
  }

  Widget _buildPriceRow(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final displayPriceLabel = selectedVariant != null
        ? selectedVariant!.displayPriceLabel
        : product.formattedDisplayPrice;
    final originalPrice = selectedVariant != null
        ? selectedVariant!.originalPrice
        : product.originalPrice;
    final originalPriceLabel = selectedVariant != null
        ? selectedVariant!.originalPriceLabel
        : product.formattedOriginalPrice;
    final displayPrice = selectedVariant != null
        ? selectedVariant!.displayPrice
        : product.displayPrice;

    // Discount percentage
    final discountPercent = (originalPrice != null && originalPrice > 0)
        ? ((originalPrice - displayPrice) / originalPrice * 100).round()
        : product.discountPercent;

    return Wrap(
      spacing: 6,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        // Current price (Text-1: 24px bold)
        Text(displayPriceLabel, style: AppTextStyles.text1(context)),

        // Original price strikethrough
        if (originalPrice != null)
          Text(
            originalPriceLabel ?? '',
            style: TextStyle(
              fontFamily: 'Roboto',
              fontWeight: FontWeight.w600,
              fontSize: 16,
              height: 1.17,
              color: AppColors.neutral500,
              decoration: TextDecoration.lineThrough,
            ),
          ),

        // Discount percentage
        if (discountPercent != null && discountPercent > 0)
          Text(
            l10n.productDiscountOff(discountPercent.toString()),
            style: TextStyle(
              fontFamily: 'Roboto',
              fontWeight: FontWeight.w600,
              fontSize: 16,
              height: 1.17,
              color: AppColors.primary500,
            ),
          ),
      ],
    );
  }

  Widget _buildRatingGroup(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final rating = product.averageRating;
    final count = product.reviewCount;

    return Row(
      children: [
        // ── Green rating badge ──
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
          decoration: BoxDecoration(
            color: AppColors.successGreen,
            borderRadius: BorderRadius.circular(6),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.star, size: 16, color: AppColors.white),
              const SizedBox(width: 1),
              Text(
                rating > 0 ? rating.toStringAsFixed(1) : '0.0',
                style: const TextStyle(
                  fontFamily: 'Roboto',
                  fontSize: 14,
                  fontWeight: FontWeight.w400,
                  color: AppColors.white,
                ),
              ),
            ],
          ),
        ),

        const SizedBox(width: 4),

        // ── Review count ──
        Text(
          '$count',
          style: TextStyle(
            fontFamily: 'Roboto',
            fontSize: 16,
            fontWeight: FontWeight.w600,
            color: isDark ? AppColors.neutral100 : AppColors.black,
          ),
        ),
      ],
    );
  }

  Widget _buildStockChip(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final inStock = product.isSaleable ?? true;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
      decoration: BoxDecoration(
        color: isDark
            ? const Color(0xFF008236) // status-success/700
            : const Color(0xFFDCFCE7), // status-success/100
        border: Border.all(
          color: isDark
              ? const Color(0xFF0D542B) // status-success/900
              : const Color(0xFFB9F8CF), // status-success/200
        ),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        inStock ? l10n.productInStock : l10n.productOutOfStock,
        style: TextStyle(
          fontFamily: 'Roboto',
          fontSize: 12,
          fontWeight: FontWeight.w400,
          color: isDark
              ? const Color(0xFFF0FDF4) // status-success/50
              : const Color(0xFF00A63E), // status-success/600
        ),
      ),
    );
  }
}
