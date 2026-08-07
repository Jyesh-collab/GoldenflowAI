// WebdriverIO Page Object. Cross-platform suites commonly mirror the same screen
// in more than one language, which is why Phase 3 must handle a Page Object class
// name appearing in two files.

class SearchScreen {
    get queryField() { return $('~search_query_field'); }
    get submitButton() { return $('~search_submit'); }
    get resultRows() { return $$('~search_result_row'); }
    get emptyState() { return $('~search_empty_state'); }

    async searchFor(term) {
        await this.queryField.setValue(term);
        await this.submitButton.click();
    }

    async openResult(index) {
        const rows = await this.resultRows;
        await rows[index].click();
    }

    async resultCount() {
        return (await this.resultRows).length;
    }
}

module.exports = new SearchScreen();
