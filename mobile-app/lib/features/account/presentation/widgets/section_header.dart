import 'package:flutter/material.dart';
import '../../../../core/theme/app_theme.dart';

/// Reusable section header with title and "View All" arrow button
/// Figma: Used across all dashboard sections
class SectionHeader extends StatelessWidget {
  final String title;
  final VoidCallback? onViewAll;

  const SectionHeader({
    super.key,
    required this.title,
    this.onViewAll,
  });

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    const arrowSize = 20.0;
    const tapTargetSize = 44.0;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(
            title,
            style: TextStyle(
              fontFamily: 'Roboto',
              fontWeight: FontWeight.w600,
              fontSize: 14,
              color: isDark ? AppColors.neutral200 : AppColors.neutral800,
            ),
          ),
          if (onViewAll != null)
            Semantics(
              button: true,
              child: Material(
                color: Colors.transparent,
                child: InkResponse(
                  onTap: onViewAll,
                  radius: tapTargetSize / 2,
                  containedInkWell: false,
                  highlightShape: BoxShape.circle,
                  splashColor: AppColors.primary500.withValues(alpha: 0.16),
                  highlightColor: AppColors.primary500.withValues(alpha: 0.08),
                  child: SizedBox(
                    width: tapTargetSize,
                    height: tapTargetSize,
                    child: Center(
                      child: Icon(
                        Icons.arrow_circle_right,
                        size: arrowSize,
                        color: AppColors.primary500,
                      ),
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
