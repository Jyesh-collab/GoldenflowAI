import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import '../../../../core/theme/app_theme.dart';
import '../../../category/data/models/product_model.dart';
import '../bloc/product_detail_bloc.dart';

class ProductBundleOptionsSection extends StatelessWidget {
  final ProductModel product;

  const ProductBundleOptionsSection({super.key, required this.product});

  @override
  Widget build(BuildContext context) {
    final options = List<BundleOption>.from(product.bundleOptions)
      ..sort((a, b) => (a.sortOrder ?? 0).compareTo(b.sortOrder ?? 0));

    if (options.isEmpty) return const SizedBox.shrink();

    return BlocBuilder<ProductDetailBloc, ProductDetailState>(
      buildWhen: (previous, current) =>
          previous.selectedBundleOptions != current.selectedBundleOptions ||
          previous.bundleOptionQuantities != current.bundleOptionQuantities,
      builder: (context, state) {
        return Column(
          children: options
              .map(
                (option) => Padding(
                  padding: const EdgeInsets.only(bottom: 14),
                  child: _BundleOptionCard(
                    option: option,
                    selectedIds:
                        state.selectedBundleOptions[option.id] ?? const [],
                    selectedQuantities: state.bundleOptionQuantities,
                  ),
                ),
              )
              .toList(),
        );
      },
    );
  }
}

class _BundleOptionCard extends StatelessWidget {
  final BundleOption option;
  final List<String> selectedIds;
  final Map<String, int> selectedQuantities;

  const _BundleOptionCard({
    required this.option,
    required this.selectedIds,
    required this.selectedQuantities,
  });

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final type = option.type?.toLowerCase() ?? '';
    final isMultiSelect = type.contains('checkbox') || type.contains('multi');
    final isDropdown = type == 'select' || type == 'dropdown';

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: isDark ? AppColors.neutral800 : AppColors.neutral50,
        border: Border.all(
          color: isDark ? AppColors.neutral700 : AppColors.neutral200,
        ),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  option.label ?? 'Option',
                  style: TextStyle(
                    fontFamily: 'Roboto',
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: isDark ? AppColors.neutral100 : AppColors.neutral900,
                  ),
                ),
              ),
              if (option.isRequired)
                Text(
                  'Required',
                  style: TextStyle(
                    fontFamily: 'Roboto',
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                    color: AppColors.primary500,
                  ),
                ),
            ],
          ),
          const SizedBox(height: 10),
          if (isDropdown)
            _BundleDropdown(
              option: option,
              selectedId: selectedIds.isNotEmpty ? selectedIds.first : null,
              selectedQuantities: selectedQuantities,
            )
          else
            Column(
              children: option.bundleOptionProducts
                  .map(
                    (item) => Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: _BundleOptionProductTile(
                        optionId: option.id,
                        productItem: item,
                        isSelected: selectedIds.contains(item.id),
                        isMultiSelect: isMultiSelect,
                        quantity:
                            selectedQuantities[item.id] ??
                            ((item.qty ?? 1) > 0 ? (item.qty ?? 1) : 1),
                      ),
                    ),
                  )
                  .toList(),
            ),
        ],
      ),
    );
  }
}

class _BundleDropdown extends StatelessWidget {
  final BundleOption option;
  final String? selectedId;
  final Map<String, int> selectedQuantities;

  const _BundleDropdown({
    required this.option,
    required this.selectedId,
    required this.selectedQuantities,
  });

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final sortedItems = List<BundleOptionProduct>.from(
      option.bundleOptionProducts,
    )..sort((a, b) => (a.sortOrder ?? 0).compareTo(b.sortOrder ?? 0));

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        DropdownButtonFormField<String>(
          isExpanded: true,
          initialValue: sortedItems.any((item) => item.id == selectedId)
              ? selectedId
              : null,
          decoration: InputDecoration(
            filled: true,
            fillColor: isDark ? AppColors.neutral900 : AppColors.white,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: BorderSide(
                color: isDark ? AppColors.neutral700 : AppColors.neutral200,
              ),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: BorderSide(
                color: isDark ? AppColors.neutral700 : AppColors.neutral200,
              ),
            ),
          ),
          hint: const Text(
            'Select option',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          items: sortedItems
              .map(
                (item) => DropdownMenuItem<String>(
                  value: item.id,
                  child: SizedBox(
                    width: double.infinity,
                    child: Text(
                      '${item.product?.name ?? ''} - ${item.product?.formattedDisplayPrice ?? ''}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ),
              )
              .toList(),
          selectedItemBuilder: (context) {
            return sortedItems
                .map(
                  (item) => Align(
                    alignment: Alignment.centerLeft,
                  child: Text(
                      '${item.product?.name ?? ''} - ${item.product?.formattedDisplayPrice ?? ''}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                )
                .toList();
          },
          onChanged: (value) {
            if (value == null) return;
            final selectedItem = sortedItems.firstWhere(
              (item) => item.id == value,
            );
            context.read<ProductDetailBloc>().add(
              SelectBundleOptionProduct(
                bundleOptionId: option.id,
                bundleOptionProductId: value,
                isMultiSelect: false,
                defaultQty: selectedItem.qty ?? 1,
              ),
            );
          },
        ),
        if (selectedId != null)
          Builder(
            builder: (context) {
              final selectedItem = sortedItems.firstWhere(
                (item) => item.id == selectedId,
                orElse: () => sortedItems.first,
              );
              final selectedQty =
                  selectedQuantities[selectedItem.id] ??
                  ((selectedItem.qty ?? 1) > 0 ? (selectedItem.qty ?? 1) : 1);
              return Padding(
                padding: const EdgeInsets.only(top: 10),
                child: _BundleSelectedPreview(
                  optionId: option.id,
                  item: selectedItem,
                  quantity: selectedQty,
                ),
              );
            },
          ),
      ],
    );
  }
}

class _BundleSelectedPreview extends StatelessWidget {
  final String optionId;
  final BundleOptionProduct item;
  final int quantity;

  const _BundleSelectedPreview({
    required this.optionId,
    required this.item,
    required this.quantity,
  });

  @override
  Widget build(BuildContext context) {
    return _BundleOptionProductTile(
      optionId: optionId,
      productItem: item,
      isSelected: true,
      isMultiSelect: false,
      quantity: quantity,
    );
  }
}

class _BundleOptionProductTile extends StatelessWidget {
  final String optionId;
  final BundleOptionProduct productItem;
  final bool isSelected;
  final bool isMultiSelect;
  final int quantity;

  const _BundleOptionProductTile({
    required this.optionId,
    required this.productItem,
    required this.isSelected,
    required this.isMultiSelect,
    required this.quantity,
  });

  @override
  Widget build(BuildContext context) {
    final product = productItem.product;
    if (product == null) return const SizedBox.shrink();

    final isDark = Theme.of(context).brightness == Brightness.dark;
    final imageUrl = product.allImageUrls.isNotEmpty
        ? product.allImageUrls.first
        : null;
    final priceLabel = product.formattedDisplayPrice;

    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: isSelected
              ? AppColors.primary500
              : (isDark ? AppColors.neutral700 : AppColors.neutral200),
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.all(10),
        child: Column(
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                GestureDetector(
                  onTap: () {
                    context.read<ProductDetailBloc>().add(
                      SelectBundleOptionProduct(
                        bundleOptionId: optionId,
                        bundleOptionProductId: productItem.id,
                        isMultiSelect: isMultiSelect,
                        defaultQty: productItem.qty ?? 1,
                      ),
                    );
                  },
                  child: Icon(
                    isMultiSelect
                        ? (isSelected
                              ? Icons.check_box
                              : Icons.check_box_outline_blank)
                        : (isSelected
                              ? Icons.radio_button_checked
                              : Icons.radio_button_unchecked),
                    color: isSelected
                        ? AppColors.primary500
                        : (isDark
                              ? AppColors.neutral500
                              : AppColors.neutral400),
                    size: 22,
                  ),
                ),
                const SizedBox(width: 8),
                ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: Container(
                    width: 52,
                    height: 52,
                    color: isDark ? AppColors.neutral700 : AppColors.neutral100,
                    child: imageUrl == null
                        ? Icon(
                            Icons.image_not_supported_outlined,
                            size: 20,
                            color: isDark
                                ? AppColors.neutral500
                                : AppColors.neutral400,
                          )
                        : CachedNetworkImage(
                            imageUrl: imageUrl,
                            fit: BoxFit.cover,
                            errorWidget: (context, url, error) => Icon(
                              Icons.broken_image_outlined,
                              size: 20,
                              color: isDark
                                  ? AppColors.neutral500
                                  : AppColors.neutral400,
                            ),
                          ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        product.name ?? '',
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontFamily: 'Roboto',
                          fontSize: 13,
                          fontWeight: FontWeight.w500,
                          color: isDark
                              ? AppColors.neutral100
                              : AppColors.neutral900,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        priceLabel,
                        style: TextStyle(
                          fontFamily: 'Roboto',
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                          color: AppColors.primary500,
                        ),
                      ),
                      if (productItem.isDefault)
                        Text(
                          'Default',
                          style: TextStyle(
                            fontFamily: 'Roboto',
                            fontSize: 11,
                            fontWeight: FontWeight.w500,
                            color: isDark
                                ? AppColors.neutral500
                                : AppColors.neutral400,
                          ),
                        ),
                    ],
                  ),
                ),
              ],
            ),
            if (isSelected && productItem.isUserDefined)
              Align(
                alignment: Alignment.centerRight,
                child: Padding(
                  padding: const EdgeInsets.only(top: 10),
                  child: SizedBox(
                    width: 118,
                    child: Row(
                      children: [
                        _qtyAction(
                          context,
                          icon: Icons.remove,
                          onTap: quantity > 1
                              ? () {
                                  context.read<ProductDetailBloc>().add(
                                    UpdateBundleOptionProductQuantity(
                                      bundleOptionProductId: productItem.id,
                                      quantity: quantity - 1,
                                    ),
                                  );
                                }
                              : null,
                        ),
                        Expanded(
                          child: Center(
                            child: Text(
                              '$quantity',
                              style: TextStyle(
                                fontFamily: 'Roboto',
                                fontSize: 14,
                                fontWeight: FontWeight.w500,
                                color: isDark
                                    ? AppColors.neutral100
                                    : AppColors.neutral900,
                              ),
                            ),
                          ),
                        ),
                        _qtyAction(
                          context,
                          icon: Icons.add,
                          onTap: () {
                            context.read<ProductDetailBloc>().add(
                              UpdateBundleOptionProductQuantity(
                                bundleOptionProductId: productItem.id,
                                quantity: quantity + 1,
                              ),
                            );
                          },
                        ),
                      ],
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _qtyAction(
    BuildContext context, {
    required IconData icon,
    required VoidCallback? onTap,
  }) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 28,
        height: 28,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(6),
          border: Border.all(
            color: isDark ? AppColors.neutral700 : AppColors.neutral200,
          ),
        ),
        alignment: Alignment.center,
        child: Icon(
          icon,
          size: 16,
          color: onTap == null
              ? (isDark ? AppColors.neutral700 : AppColors.neutral300)
              : (isDark ? AppColors.neutral100 : AppColors.neutral900),
        ),
      ),
    );
  }
}
