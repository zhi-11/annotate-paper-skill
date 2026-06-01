// Test: text annotation matching manual format exactly
const readers = Zotero.Reader._readers;
if (!readers || readers.length === 0) {
    Services.prompt.alert(null, "Error", "No active PDF reader");
} else {
    const reader = readers[0];
    const attachment = Zotero.Items.get(reader.itemID);

    const pageIndex = 0;
    const rect = [350, 100, 500, 140];
    const y = Math.round(rect[1]);
    const si = String(pageIndex).padStart(5, "0") + "|" + String(y).padStart(6, "0") + "|00000";

    const ann = new Zotero.Item("annotation");
    ann.libraryID = attachment.libraryID;
    ann.parentItemID = attachment.id;
    ann.annotationType = "text";
    ann.annotationComment = "这是一条测试段落概述";
    ann.annotationColor = "#f19837";
    ann.annotationPageLabel = "1";
    ann.annotationPosition = JSON.stringify({
        pageIndex: pageIndex,
        fontSize: 12,
        rotation: 0,
        rects: [[rect[0], rect[1], rect[2], rect[3]]]
    });
    ann.annotationSortIndex = si;
    ann.addTag("#ai段落概述");

    const id = await ann.saveTx();
    Services.prompt.alert(null, "Done", "Created id=" + id + " on page " + (pageIndex + 1));
}
