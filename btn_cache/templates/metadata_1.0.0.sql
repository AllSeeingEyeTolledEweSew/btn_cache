{%- macro abort() -%}
SELECT RAISE(ABORT, '{{ caller() }}')
{%- endmacro -%}

{%- macro next_updated_at() -%}
SELECT MAX(u) + 1 FROM (SELECT MAX(updated_at) AS u FROM series{%- if DEBUG %} INDEXED BY series_on_updated_at{%- endif %} UNION ALL SELECT MAX(updated_at) AS u FROM torrent_entry_group{%- if DEBUG %} INDEXED BY torrent_entry_group_on_updated_at{%- endif %} UNION ALL SELECT MAX(updated_at) AS u FROM torrent_entry{%- if DEBUG %} INDEXED BY torrent_entry_on_updated_at{%- endif %})
{%- endmacro -%}

CREATE TABLE "{schema}".series (id INTEGER PRIMARY KEY, imdb_id TEXT, name TEXT, banner TEXT, poster TEXT, tvdb_id INTEGER, tvrage_id INTEGER, youtube_trailer TEXT, updated_at INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL);

CREATE INDEX "{schema}".series_on_updated_at ON series (updated_at);

CREATE TRIGGER "{schema}".series_delete_abort BEFORE DELETE ON series BEGIN {% call abort() %}delete on series is disabled{% endcall %}; END;
CREATE TRIGGER "{schema}".series_change_rowid_abort BEFORE UPDATE OF id ON series WHEN new.id != old.id BEGIN {% call abort() %}changing series.id is disabled{% endcall %}; END;
CREATE TRIGGER "{schema}".series_insert_set_updated_at AFTER INSERT ON series BEGIN UPDATE series SET updated_at = ({{ next_updated_at() }}) WHERE id = new.id; END;
CREATE TRIGGER "{schema}".series_update_set_updated_at AFTER UPDATE OF imdb_id, name, banner, poster, tvdb_id, tvrage_id, youtube_trailer, deleted ON series WHEN {%+ for col in ("imdb_id", "name", "banner", "poster", "tvdb_id", "tvrage_id", "youtube_trailer") -%}old.{{col}} IS NOT new.{{col}}{{ " OR " if not loop.last }}{%- endfor %} BEGIN UPDATE series SET updated_at = ({{ next_updated_at() }}) WHERE id = new.id; END;

CREATE TABLE "{schema}".torrent_entry_group (id INTEGER PRIMARY KEY, category TEXT NOT NULL, name TEXT, series_id INTEGER NOT NULL, updated_at INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL);

CREATE INDEX "{schema}".torrent_entry_group_on_updated_at ON torrent_entry_group (updated_at);
CREATE INDEX "{schema}".torrent_entry_group_on_series_id ON torrent_entry_group (series_id);

CREATE TRIGGER "{schema}".torrent_entry_group_delete_abort BEFORE DELETE ON torrent_entry_group BEGIN {% call abort() %}delete on torrent_entry_group is disabled{% endcall %}; END;
CREATE TRIGGER "{schema}".torrent_entry_group_change_rowid_abort BEFORE UPDATE OF id ON torrent_entry_group WHEN new.id != old.id BEGIN {% call abort() %}changing torrent_entry_group.id is disabled{% endcall %}; END;
CREATE TRIGGER "{schema}".torrent_entry_group_insert_set_updated_at AFTER INSERT ON torrent_entry_group BEGIN UPDATE torrent_entry_group SET updated_at = ({{ next_updated_at() }}) WHERE id = new.id; END;
CREATE TRIGGER "{schema}".torrent_entry_group_update_set_updated_at AFTER UPDATE OF category, name, series_id, deleted ON torrent_entry_group WHEN {%+ for col in ("category", "name", "series_id", "deleted") -%}old.{{col}} IS NOT new.{{col}}{{ " OR " if not loop.last}}{%- endfor %} BEGIN UPDATE torrent_entry_group SET updated_at = ({{ next_updated_at() }}) WHERE id = new.id; END;
CREATE TRIGGER "{schema}".torrent_entry_group_update_check_series_deleted AFTER UPDATE OF series_id, deleted ON torrent_entry_group WHEN (new.series_id != old.series_id OR new.deleted != old.deleted) AND NOT EXISTS (SELECT t.series_id FROM torrent_entry_group t {%+ if DEBUG -%}INDEXED BY torrent_entry_group_on_series_id {%+ endif -%} WHERE t.series_id = old.series_id AND NOT t.deleted) BEGIN UPDATE series SET deleted = 1 WHERE id = old.series_id; END;

CREATE TABLE "{schema}".torrent_entry (id INTEGER PRIMARY KEY, codec TEXT, container TEXT, group_id INTEGER NOT NULL, info_hash TEXT NOT NULL COLLATE NOCASE, origin TEXT, release_name TEXT, resolution TEXT, size INTEGER NOT NULL, source TEXT, time INTEGER NOT NULL, snatched INTEGER NOT NULL, seeders INTEGER NOT NULL, leechers INTEGER NOT NULL, updated_at INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL);

CREATE INDEX "{schema}".torrent_entry_on_updated_at ON torrent_entry (updated_at);
CREATE INDEX "{schema}".torrent_entry_on_group_id ON torrent_entry (group_id);
CREATE INDEX "{schema}".torrent_entry_on_time ON torrent_entry (time);

CREATE TRIGGER "{schema}".torrent_entry_delete_abort BEFORE DELETE ON torrent_entry BEGIN {% call abort() %}delete on torrent_entry is disabled{% endcall %}; END;
CREATE TRIGGER "{schema}".torrent_entry_change_rowid_abort BEFORE UPDATE OF id ON torrent_entry WHEN new.id != old.id BEGIN {% call abort() %}changing torrent_entry.id is disabled{% endcall %}; END;
CREATE TRIGGER "{schema}".torrent_entry_insert_set_updated_at AFTER INSERT ON torrent_entry BEGIN UPDATE torrent_entry SET updated_at = ({{ next_updated_at() }}) WHERE id = new.id; END;
CREATE TRIGGER "{schema}".torrent_entry_update_set_updated_at AFTER UPDATE OF codec, container, group_id, info_hash, origin, release_name, resolution, size, source, time, deleted ON torrent_entry WHEN {%+ for col in ("codec", "container", "group_id", "info_hash", "origin", "release_name", "resolution", "size", "source", "time", "deleted") -%}old.{{col}} IS NOT new.{{col}}{{ " OR " if not loop.last }}{%- endfor %} BEGIN UPDATE torrent_entry SET updated_at = ({{ next_updated_at() }}) WHERE id = new.id; END;
CREATE TRIGGER "{schema}".torrent_entry_update_check_group_deleted AFTER UPDATE OF series_id, deleted ON torrent_entry WHEN (new.group_id != old.group_id OR new.deleted != old.deleted) AND NOT EXISTS (SELECT t.group_id FROM torrent_entry t {%+ if DEBUG -%} INDEXED BY torrent_entry_on_group_id {%+ endif -%} WHERE t.group_id = old.group_id AND NOT t.deleted) BEGIN UPDATE torrent_entry_group SET deleted = 1 WHERE id = old.group_id; END;

CREATE TABLE "{schema}".file_info (id INTEGER NOT NULL, file_index INTEGER NOT NULL, path BLOB NOT NULL, encoding TEXT COLLATE NOCASE, start INTEGER NOT NULL, stop INTEGER NOT NULL);

CREATE UNIQUE INDEX "{schema}".file_info_on_id_and_file_index ON file_info (id, file_index);

CREATE TRIGGER "{schema}".file_info_change_unique_abort BEFORE UPDATE OF id ON file_info WHEN new.id != old.id BEGIN {% call abort() %}changing file_info.id is disabled{% endcall %}; END;
CREATE TRIGGER "{schema}".file_info_insert_set_updated_at AFTER INSERT ON file_info BEGIN UPDATE torrent_entry SET updated_at = ({{ next_updated_at() }}) WHERE id = new.id; END;
CREATE TRIGGER "{schema}".file_info_update_set_updated_at AFTER UPDATE OF file_index, path, encoding, start, stop ON file_info WHEN {%+ for col in ("file_index", "path", "encoding", "start", "stop") -%}old.{{col}} IS NOT new.{{col}}{{ " OR " if not loop.last }}{%- endfor %} BEGIN UPDATE torrent_entry SET updated_at = ({{ next_updated_at() }}) WHERE id = new.id; END;
CREATE TRIGGER "{schema}".file_info_delete_set_updated_at AFTER DELETE ON file_info BEGIN UPDATE torrent_entry SET updated_at = ({{ next_updated_at() }}) WHERE id = old.id; END;
