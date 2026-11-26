.pragma library
// Display coordinates only: no files, services, titles or imported metadata.
function valid(rect) {
    return rect && [rect.x, rect.y, rect.width, rect.height].every(function(v) {
        return typeof v === "number" && isFinite(v)
    }) && rect.width > 0 && rect.height > 0
}
function bound(value, low, high) { return Math.max(low, Math.min(high, value)) }
function axis(value, low, high, snap, grid, threshold) {
    value = bound(value, low, high)
    if (!snap) return Math.round(value)
    if (Math.abs(value - low) <= threshold) return low
    if (Math.abs(value - high) <= threshold) return high
    return bound(low + Math.round((value - low) / grid) * grid, low, high)
}
function fit(rect, area, minWidth, minHeight, maxWidth, maxHeight, snap, snapSize) {
    if (!valid(area)) area = {x: 0, y: 0, width: 800, height: 600}
    if (!valid(rect)) rect = {x: area.x, y: area.y, width: 320, height: 240}
    var w = bound(rect.width, Math.min(minWidth, area.width), Math.min(maxWidth, area.width))
    var h = bound(rect.height, Math.min(minHeight, area.height), Math.min(maxHeight, area.height))
    if (snapSize === true) {
        w = bound(Math.round(w / 16) * 16, Math.min(minWidth, area.width), Math.min(maxWidth, area.width))
        h = bound(Math.round(h / 16) * 16, Math.min(minHeight, area.height), Math.min(maxHeight, area.height))
    }
    return {x: axis(rect.x, area.x, area.x + area.width - w, snap, 16, 18),
            y: axis(rect.y, area.y, area.y + area.height - h, snap, 16, 18), width: w, height: h}
}
function restore(record, fallback, area, minWidth, minHeight, maxWidth, maxHeight) {
    var rect = fallback
    if (record && record.version === 1 && valid(record.rect) && valid(record.area)) {
        rect = {x: record.rect.x, y: record.rect.y, width: record.rect.width, height: record.rect.height}
        if (record.area.x !== area.x || record.area.y !== area.y || record.area.width !== area.width || record.area.height !== area.height) {
            var rx = (record.rect.x - record.area.x) / Math.max(1, record.area.width - record.rect.width)
            var ry = (record.rect.y - record.area.y) / Math.max(1, record.area.height - record.rect.height)
            rect.x = area.x + bound(rx, 0, 1) * Math.max(0, area.width - Math.min(rect.width, area.width))
            rect.y = area.y + bound(ry, 0, 1) * Math.max(0, area.height - Math.min(rect.height, area.height))
        }
    }
    return fit(rect, area, minWidth, minHeight, maxWidth, maxHeight, false)
}
