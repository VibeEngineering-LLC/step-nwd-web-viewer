#include <nwd/reader.hpp>
#include <iostream>
#include <string>
#include <vector>
#include <cstdint>
#include <filesystem>
#include <stdexcept>

namespace {

std::string get_name(const nwd::ObjectGraph& g, nwd::Id id) {
    if (id < g.objects.size() && g.objects[id].strings.size() >= 1) {
        return std::string(nwd::resolve_string(g, g.objects[id].strings[0]));
    }
    return "";
}

std::string format_value(const nwd::ObjectGraph& g, const nwd::Property& p) {
    uint32_t tag = p.value.tag;
    if (tag == 4 || tag == 9) {
        return std::string(nwd::resolve_string(g, p.value.string));
    }
    if (tag == 2 || tag == 3 || tag == 5 || tag == 14 || tag == 15 || tag == 16) {
        return std::to_string(p.value.integer);
    }
    if (tag == 1 || tag == 6 || tag == 7 || tag == 10 || tag == 11 || tag == 12 || tag == 13) {
        return std::to_string(p.value.number[0]);
    }
    return "tag" + std::to_string(tag);
}

void print_node(const nwd::Model& model, nwd::Id path_id, int level) {
    std::string node_name;
    try {
        nwd::Reference r = nwd::path_reference(model, path_id);
        const nwd::Object& node = nwd::resolve_object(model, r);
        const nwd::ObjectGraph& g = model.graphs.at(r.graph);
        node_name = get_name(g, node.name);
    } catch (const std::exception&) {
        node_name = "?";
    }

    std::cout << "== path " << path_id << " up" << level << " " << node_name << "\n";

    try {
        nwd::Reference r = nwd::path_reference(model, path_id);
        const nwd::Object& node = nwd::resolve_object(model, r);

        for (const nwd::Reference& attr_ref : node.attributes) {
            std::string category_name;
            std::vector<std::pair<std::string, std::string>> props;

            try {
                const nwd::Object& attr_obj = nwd::resolve_object(model, attr_ref);
                const nwd::ObjectGraph& g = model.graphs.at(attr_ref.graph);
                category_name = get_name(g, attr_obj.name);

                for (const nwd::Property& prop : attr_obj.properties) {
                    std::string prop_name = get_name(g, prop.name);
                    std::string val;
                    try {
                        val = format_value(g, prop);
                    } catch (const std::exception&) {
                        val = "?";
                    }
                    props.push_back({prop_name, val});
                }
            } catch (const std::exception&) {
                category_name = "?";
            }

            std::cout << " [" << category_name << "]\n";
            for (const auto& [name, val] : props) {
                std::cout << "   " << name << " = " << val << "\n";
            }
        }
    } catch (const std::exception&) {
        // ignore
    }
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    try {
        if (argc < 3) {
            std::cerr << "Usage: nwd_probe INPUT.nwd PATH_ID [PATH_ID ...]\n";
            return 1;
        }

        std::filesystem::path input_path(argv[1]);
        nwd::Document doc(input_path);
        nwd::Scene scene = doc.read_scene();

        if (scene.models.empty()) {
            std::cerr << "No models found in scene\n";
            return 1;
        }

        const nwd::Model& model = scene.models[0];

        for (int i = 2; i < argc; ++i) {
            nwd::Id path_id;
            try {
                path_id = static_cast<nwd::Id>(std::stoul(std::wstring(argv[i])));
            } catch (const std::exception&) {
                std::cerr << "Invalid path id\n";
                continue;
            }

            nwd::Id current = path_id;
            int level = 0;
            while (current != nwd::none && level < 3) {
                print_node(model, current, level);
                try {
                    current = model.paths.at(current).parent;
                } catch (const std::exception&) {
                    break;
                }
                ++level;
            }
        }
    } catch (const std::exception& e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
    return 0;
}
