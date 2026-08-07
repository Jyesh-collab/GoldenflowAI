// WebdriverIO search specs. Exercises the heuristic JavaScript parser.

const HomeScreen = require('../pageobjects/HomeScreen');
const SearchScreen = require('../pageobjects/SearchScreen');
const ProductDetailScreen = require('../pageobjects/ProductDetailScreen');

describe('Search', () => {
    it('returns results for a common term', async () => {
        const home = new HomeScreen();
        await home.openSearch();

        const search = new SearchScreen();
        await search.searchFor('trainers');
        await expect(await search.resultCount()).toBeGreaterThan(0);
    });

    it('opens a product from the results list', async () => {
        const search = new SearchScreen();
        await search.searchFor('socks');
        await search.openResult(0);

        const pdp = new ProductDetailScreen();
        await expect(pdp.title).toBeDisplayed();
    });

    it.skip('handles zero results gracefully', async () => {
        const search = new SearchScreen();
        await search.searchFor('zzzzzzqqqq');
        await expect(await search.resultCount()).toBe(0);
    });
});
