#pragma once
#include <nwd/reader.hpp>
#include <string>

inline std::string find_property(const nwd::Model& m, nwd::Id path, const std::string& wanted) {
    try {
        nwd::Id current = path;
        for (int i = 0; i < 4 && current != nwd::none; ++i) {
            if (current >= m.paths.size()) break;
            const nwd::Path& p = m.paths[current];
            nwd::Reference r = nwd::path_reference(m, current);
            const nwd::Object& node = nwd::resolve_object(m, r);
            for (const nwd::Reference& a : node.attributes) {
                if (a.graph >= m.graphs.size()) continue;
                const nwd::ObjectGraph& g = m.graphs[a.graph];
                const nwd::Object& o = nwd::resolve_object(m, a);
                for (const nwd::Property& q : o.properties) {
                    if (q.name >= g.objects.size()) continue;
                    const nwd::Object& nameObj = g.objects[q.name];
                    if (nameObj.strings.empty()) continue;
                    std::string_view displayName = nwd::resolve_string(g, nameObj.strings[0]);
                    if (displayName == wanted) {
                        if (q.value.tag == 4 && q.value.string != nwd::none) {
                            return std::string(nwd::resolve_string(g, q.value.string));
                        }
                    }
                }
            }
            current = p.parent;
        }
    } catch (...) {
    }
    return {};
}
