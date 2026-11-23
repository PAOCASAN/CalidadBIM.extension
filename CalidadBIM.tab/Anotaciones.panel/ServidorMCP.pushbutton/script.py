# -*- coding: utf-8 -*-
"""
Calidad BIM: Servidor Puente MCP para Revit 2026
Inicia un listener HTTP local en http://127.0.0.1:8050 con un ExternalEvent
para permitir que Antigravity / Claude / Agentes MCP ejecuten comandos de forma segura.
"""

import sys
import json
import socket
import threading
import unicodedata
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from pyrevit import revit, DB, UI, forms

PORT = 8050
_SERVER_INSTANCE = None
_SERVER_THREAD = None

class McpBridgeHandler(UI.IExternalEventHandler):
    def __init__(self):
        self.pending_func = None
        self.result = None
        self.error = None
        self.done_event = threading.Event()

    def Execute(self, uiapp):
        try:
            if self.pending_func:
                self.result = self.pending_func(uiapp)
            self.error = None
        except Exception as ex:
            self.error = str(ex)
            self.result = None
        finally:
            self.done_event.set()

    def GetName(self):
        return "PyRevitMcpBridgeHandler"

_BRIDGE_HANDLER = McpBridgeHandler()
_EXTERNAL_EVENT = UI.ExternalEvent.Create(_BRIDGE_HANDLER)

def run_in_revit(func, timeout=25.0):
    _BRIDGE_HANDLER.done_event.clear()
    _BRIDGE_HANDLER.pending_func = func
    _BRIDGE_HANDLER.result = None
    _BRIDGE_HANDLER.error = None

    _EXTERNAL_EVENT.Raise()
    finished = _BRIDGE_HANDLER.done_event.wait(timeout)

    if not finished:
        raise RuntimeError("Timeout esperando a Revit API.")
    if _BRIDGE_HANDLER.error:
        raise RuntimeError(_BRIDGE_HANDLER.error)
    return _BRIDGE_HANDLER.result

TARGET_TAG_FAMILIES = ["SHYD1_TAG_FRN", "SHYD1_TAG_MED", "SHYD1_TAG_TV"]
TARGET_TAG_TYPE_200 = "SHYD1_ETIQUETA_1-200"
DETAIL_COMPONENT_NAME = "SHYD1_VIÑETA EDITABLE"
GENERIC_ANNOTATION_NAME = "SHYD1_ANOTACION EDITABLE"

TARGET_HIDDEN_BCATS = [
    DB.BuiltInCategory.OST_MedicalEquipment,
    DB.BuiltInCategory.OST_Furniture,
    DB.BuiltInCategory.OST_GenericModel,
    DB.BuiltInCategory.OST_IOSModelGroups
]

CAT_NAMES_MAP = {
    int(DB.BuiltInCategory.OST_MedicalEquipment): "Equipo Médico",
    int(DB.BuiltInCategory.OST_Furniture): "Mobiliario",
    int(DB.BuiltInCategory.OST_GenericModel): "Modelo Genérico",
    int(DB.BuiltInCategory.OST_IOSModelGroups): "Grupo de Modelo"
}

def get_id_val(elem_or_id):
    if hasattr(elem_or_id, "Id"):
        elem_or_id = elem_or_id.Id
    if hasattr(elem_or_id, "Value"):
        return elem_or_id.Value
    return elem_or_id.IntegerValue

def get_family_name(elem_type):
    if not elem_type:
        return ""
    if hasattr(elem_type, "FamilyName") and elem_type.FamilyName:
        return elem_type.FamilyName
    if hasattr(elem_type, "Family") and elem_type.Family and hasattr(elem_type.Family, "Name"):
        return elem_type.Family.Name
    try:
        p = elem_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME)
        if p and p.AsString():
            return p.AsString()
    except Exception:
        pass
    return getattr(elem_type, "Name", "")

def is_fine_line(curve, doc):
    style_name = ""
    try:
        if hasattr(curve, "LineStyle") and curve.LineStyle:
            style_name = curve.LineStyle.Name
    except Exception:
        pass

    if not style_name:
        try:
            p = curve.get_Parameter(DB.BuiltInParameter.BUILD_IN_STYLE__OVERRIDE)
            if p and p.AsElementId() != DB.ElementId.InvalidElementId:
                elem = doc.GetElement(p.AsElementId())
                if elem:
                    style_name = elem.Name
        except Exception:
            pass

    if not style_name:
        return False

    norm = style_name.strip().strip("<>").strip().upper()
    norm = "".join(c for c in unicodedata.normalize('NFD', norm) if unicodedata.category(c) != 'Mn')
    return ("LINEA" in norm and "FINA" in norm) or ("THIN" in norm and "LINE" in norm)

def find_target_tag_types(doc):
    symbols = DB.FilteredElementCollector(doc)\
        .WhereElementIsElementType()\
        .ToElements()

    type_map = {}
    for sym in symbols:
        fam_name = get_family_name(sym)
        sym_name = ""
        try:
            p = sym.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
            if p and p.AsString():
                sym_name = p.AsString()
        except Exception:
            pass

        if not sym_name:
            sym_name = getattr(sym, "Name", "")

        if fam_name in TARGET_TAG_FAMILIES and sym_name == TARGET_TAG_TYPE_200:
            type_map[fam_name] = sym.Id
    return type_map

def find_generic_annotation_200_type(doc):
    symbols = DB.FilteredElementCollector(doc)\
        .OfCategory(DB.BuiltInCategory.OST_GenericAnnotation)\
        .WhereElementIsElementType()\
        .ToElements()

    candidates = []
    for sym in symbols:
        fam = get_family_name(sym)
        sym_name = ""
        try:
            p = sym.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
            if p and p.AsString():
                sym_name = p.AsString()
        except Exception:
            pass
        if not sym_name:
            sym_name = getattr(sym, "Name", "")

        if fam == GENERIC_ANNOTATION_NAME or sym_name == GENERIC_ANNOTATION_NAME or sym.Name == GENERIC_ANNOTATION_NAME:
            candidates.append((sym, sym_name))

    for sym, name in candidates:
        norm = name.replace(" ", "").upper()
        if any(x in norm for x in ["1-200", "1/200", "1:200", "1_200"]):
            return sym.Id

    for sym, name in candidates:
        if "200" in name and "50" not in name:
            return sym.Id

    non_50 = [s for s, n in candidates if "50" not in n]
    if len(non_50) == 1:
        return non_50[0].Id

    return None

class DuplicateTypeHandler(DB.IDuplicateTypeNamesHandler):
    def OnDuplicateTypeNamesFound(self, args):
        return DB.DuplicateTypeAction.UseDestinationTypes

def revit_get_status(uiapp):
    doc = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    view = doc.ActiveView if doc else None
    return {
        "status": "ok",
        "revitVersion": uiapp.Application.VersionNumber,
        "document": doc.Title if doc else None,
        "activeView": {
            "id": get_id_val(view.Id),
            "name": view.Name,
            "scale": view.Scale,
            "viewType": str(view.ViewType)
        } if view else None
    }

def revit_list_views(uiapp):
    doc = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    if not doc:
        return {"views": []}

    views = DB.FilteredElementCollector(doc)\
        .OfClass(DB.View)\
        .WhereElementIsNotElementType()\
        .ToElements()

    result = []
    for v in views:
        if not v.IsTemplate and not v.IsAssemblyView and isinstance(v, (DB.ViewPlan, DB.ViewSection, DB.ViewDrafting, DB.View3D)):
            result.append({
                "id": get_id_val(v.Id),
                "name": v.Name,
                "scale": v.Scale,
                "viewType": str(v.ViewType)
            })
    return {"views": result}

def revit_inspect_view(uiapp, view_id_num=None):
    doc = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    if not doc:
        raise ValueError("No hay documento activo.")

    view = None
    if view_id_num:
        view = doc.GetElement(DB.ElementId(int(view_id_num)))
    if not view:
        view = doc.ActiveView

    tag_counts = {fam: 0 for fam in TARGET_TAG_FAMILIES}
    tags = DB.FilteredElementCollector(doc, view.Id)\
        .OfClass(DB.IndependentTag)\
        .WhereElementIsNotElementType()\
        .ToElements()

    for t in tags:
        tp = doc.GetElement(t.GetTypeId())
        fam = get_family_name(tp)
        if fam in tag_counts:
            tag_counts[fam] += 1

    details = DB.FilteredElementCollector(doc, view.Id)\
        .OfCategory(DB.BuiltInCategory.OST_DetailComponents)\
        .WhereElementIsNotElementType()\
        .ToElements()
    det_count = sum(1 for d in details if (getattr(d, "Symbol", None) and get_family_name(d.Symbol) == DETAIL_COMPONENT_NAME) or d.Name == DETAIL_COMPONENT_NAME)

    generics = DB.FilteredElementCollector(doc, view.Id)\
        .OfCategory(DB.BuiltInCategory.OST_GenericAnnotation)\
        .WhereElementIsNotElementType()\
        .ToElements()
    gen_count = sum(1 for g in generics if (getattr(g, "Symbol", None) and get_family_name(g.Symbol) == GENERIC_ANNOTATION_NAME) or g.Name == GENERIC_ANNOTATION_NAME)

    curves = DB.FilteredElementCollector(doc, view.Id)\
        .OfClass(DB.CurveElement)\
        .WhereElementIsNotElementType()\
        .ToElements()
    lin_count = sum(1 for c in curves if is_fine_line(c, doc))

    return {
        "viewId": get_id_val(view.Id),
        "viewName": view.Name,
        "scale": view.Scale,
        "tags": tag_counts,
        "detailComponentsCount": det_count,
        "genericAnnotationsCount": gen_count,
        "linesCount": lin_count
    }

def revit_copy_annotations(uiapp, target_view_id_num, source_view_id_num=None):
    doc = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    if not doc:
        raise ValueError("No hay documento activo.")

    source_view = None
    if source_view_id_num:
        source_view = doc.GetElement(DB.ElementId(int(source_view_id_num)))
    if not source_view:
        source_view = doc.ActiveView

    target_view = doc.GetElement(DB.ElementId(int(target_view_id_num)))
    if not target_view:
        raise ValueError("Vista destino no encontrada con ID {}".format(target_view_id_num))

    candidate_ids = []
    tags = DB.FilteredElementCollector(doc, source_view.Id)\
        .OfClass(DB.IndependentTag)\
        .WhereElementIsNotElementType()\
        .ToElements()
    for tag in tags:
        tp = doc.GetElement(tag.GetTypeId())
        if get_family_name(tp) in TARGET_TAG_FAMILIES:
            candidate_ids.append(tag.Id)

    details = DB.FilteredElementCollector(doc, source_view.Id)\
        .OfCategory(DB.BuiltInCategory.OST_DetailComponents)\
        .WhereElementIsNotElementType()\
        .ToElements()
    for d in details:
        fam = get_family_name(d.Symbol) if hasattr(d, "Symbol") and d.Symbol else get_family_name(doc.GetElement(d.GetTypeId()))
        if fam == DETAIL_COMPONENT_NAME or d.Name == DETAIL_COMPONENT_NAME:
            candidate_ids.append(d.Id)

    generics = DB.FilteredElementCollector(doc, source_view.Id)\
        .OfCategory(DB.BuiltInCategory.OST_GenericAnnotation)\
        .WhereElementIsNotElementType()\
        .ToElements()
    for g in generics:
        fam = get_family_name(g.Symbol) if hasattr(g, "Symbol") and g.Symbol else get_family_name(doc.GetElement(g.GetTypeId()))
        if fam == GENERIC_ANNOTATION_NAME or g.Name == GENERIC_ANNOTATION_NAME:
            candidate_ids.append(g.Id)

    curves = DB.FilteredElementCollector(doc, source_view.Id)\
        .OfClass(DB.CurveElement)\
        .WhereElementIsNotElementType()\
        .ToElements()
    for c in curves:
        if is_fine_line(c, doc):
            candidate_ids.append(c.Id)

    if not candidate_ids:
        return {
            "success": True,
            "message": "No se encontraron elementos coincidentes en la vista origen.",
            "totalCopied": 0
        }

    target_types = find_target_tag_types(doc)
    target_generic_type_id = find_generic_annotation_200_type(doc)

    from System.Collections.Generic import List as ClrList
    id_collection = ClrList[DB.ElementId](candidate_ids)

    copy_opts = DB.CopyPasteOptions()
    copy_opts.SetDuplicateTypeNamesHandler(DuplicateTypeHandler())

    updated_tags = 0
    updated_generics = 0
    warnings = []

    with revit.Transaction("Calidad BIM: Copiar Anotaciones (MCP API)"):
        new_ids = DB.ElementTransformUtils.CopyElements(
            source_view,
            id_collection,
            target_view,
            DB.Transform.Identity,
            copy_opts
        )

        for nid in new_ids:
            nelem = doc.GetElement(nid)
            if isinstance(nelem, DB.IndependentTag):
                ntype = doc.GetElement(nelem.GetTypeId())
                fam = get_family_name(ntype)
                if fam in target_types:
                    try:
                        nelem.ChangeTypeId(target_types[fam])
                        updated_tags += 1
                    except Exception as ex:
                        warnings.append("Error en etiqueta {}: {}".format(fam, ex))
                else:
                    warnings.append("Tipo 1:200 no encontrado para {}".format(fam))
            elif getattr(nelem, "Category", None) and nelem.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_GenericAnnotation):
                fam = get_family_name(getattr(nelem, "Symbol", None)) or get_family_name(doc.GetElement(nelem.GetTypeId()))
                if fam == GENERIC_ANNOTATION_NAME or nelem.Name == GENERIC_ANNOTATION_NAME:
                    if target_generic_type_id:
                        try:
                            nelem.ChangeTypeId(target_generic_type_id)
                            updated_generics += 1
                        except Exception as ex:
                            warnings.append("Error al cambiar tipo anotación genérica: {}".format(ex))

    return {
        "success": True,
        "message": "Anotaciones copiadas exitosamente.",
        "totalSourceFound": len(candidate_ids),
        "totalCopied": len(new_ids),
        "tagsUpdated": updated_tags,
        "genericAnnotationsUpdated": updated_generics,
        "warnings": warnings
    }

# ==========================================================
# ENDPOINTS PARA ELEMENTOS OCULTOS
# ==========================================================

def get_candidate_hidden_elements(doc):
    cat_list = [bcat for bcat in TARGET_HIDDEN_BCATS if bcat != DB.BuiltInCategory.OST_IOSModelGroups]
    cat_filter = DB.ElementMulticategoryFilter(cat_list)
    elems = list(DB.FilteredElementCollector(doc)
                 .WherePasses(cat_filter)
                 .WhereElementIsNotElementType()
                 .ToElements())
    try:
        groups = list(DB.FilteredElementCollector(doc)
                      .OfClass(DB.Group)
                      .WhereElementIsNotElementType()
                      .ToElements())
        existing_ids = {get_id_val(e) for e in elems}
        for g in groups:
            if get_id_val(g) not in existing_ids:
                elems.append(g)
    except Exception:
        pass
    return elems

def scan_view_hidden(doc, view, candidate_elements):
    if not view or view.IsTemplate or view.IsAssemblyView:
        return []

    cat_vis_map = {}
    for bcat in TARGET_HIDDEN_BCATS:
        try:
            cat = doc.Settings.Categories.get_Item(bcat)
            if cat:
                cat_vis_map[get_id_val(cat.Id)] = view.GetCategoryHidden(cat.Id)
        except Exception:
            pass

    invisible_filters = []
    try:
        for fid in view.GetFilters():
            if not view.GetFilterVisibility(fid):
                pfe = doc.GetElement(fid)
                if isinstance(pfe, DB.ParameterFilterElement):
                    invisible_filters.append((pfe, pfe.GetElementFilter()))
    except Exception:
        pass

    hidden_worksets = set()
    if doc.IsWorkshared:
        try:
            user_ws = DB.FilteredWorksetCollector(doc).OfKind(DB.WorksetKind.UserWorkset).ToWorksets()
            for ws in user_ws:
                if view.GetWorksetVisibility(ws.Id) == DB.WorksetVisibility.Hidden:
                    hidden_worksets.add(ws.Id.IntegerValue)
        except Exception:
            pass

    in_temp_hide = view.IsInTemporaryHideIsolate()
    hidden_items = []

    for elem in candidate_elements:
        try:
            if not elem.CanBeHidden(view):
                continue
        except Exception:
            continue

        cat_id_val = get_id_val(elem.Category.Id) if elem.Category else None
        is_cat_hidden = cat_vis_map.get(cat_id_val, False) if cat_id_val else False

        is_elem_hidden = False
        try:
            is_elem_hidden = elem.IsHidden(view)
        except Exception:
            pass

        is_filter_hidden = False
        filter_name = ""
        if invisible_filters and elem.Category:
            for pfe, rule in invisible_filters:
                cat_ids = [get_id_val(c) for c in pfe.GetCategories()]
                if cat_id_val in cat_ids:
                    if rule is None or rule.PassesFilter(elem):
                        is_filter_hidden = True
                        filter_name = pfe.Name
                        break

        is_ws_hidden = False
        ws_name = ""
        if doc.IsWorkshared and hasattr(elem, "WorksetId") and elem.WorksetId.IntegerValue in hidden_worksets:
            is_ws_hidden = True
            try:
                ws_name = doc.GetWorksetTable().GetWorkset(elem.WorksetId).Name
            except Exception:
                ws_name = str(elem.WorksetId.IntegerValue)

        is_temp_hidden = False
        if in_temp_hide:
            try:
                is_temp_hidden = view.IsElementHidden(elem.Id)
            except Exception:
                pass

        is_element_override = is_elem_hidden and not is_cat_hidden

        if not (is_element_override or is_cat_hidden or is_filter_hidden or is_ws_hidden or is_temp_hidden):
            continue

        reasons = []
        if is_element_override:
            reasons.append("Oculto por Elemento (Hide in View)")
        if is_cat_hidden:
            reasons.append("Categoría desactivada (VG/VV)")
        if is_filter_hidden:
            reasons.append("Filtro ({})".format(filter_name))
        if is_ws_hidden:
            reasons.append("Subproyecto ({})".format(ws_name))
        if is_temp_hidden:
            reasons.append("Aislar temporalmente (Gafas)")

        fam_name = "-"
        type_name = "-"
        type_id = elem.GetTypeId()
        if type_id and get_id_val(type_id) != -1:
            et = doc.GetElement(type_id)
            if et:
                type_name = et.Name
                fam_name = getattr(et, "FamilyName", "-")
        elif isinstance(elem, DB.Group):
            fam_name = "Grupo de Modelo"
            type_name = elem.GroupType.Name if elem.GroupType else elem.Name

        lvl_name = "-"
        if hasattr(elem, "LevelId") and get_id_val(elem.LevelId) != -1:
            lvl = doc.GetElement(elem.LevelId)
            if lvl:
                lvl_name = lvl.Name

        cat_display = "Desconocido"
        if elem.Category:
            cat_bcat = elem.Category.Id.IntegerValue
            cat_display = CAT_NAMES_MAP.get(cat_bcat, elem.Category.Name)
        elif isinstance(elem, DB.Group):
            cat_display = "Grupo de Modelo"

        hidden_items.append({
            "id": get_id_val(elem.Id),
            "category": cat_display,
            "family": fam_name,
            "type": type_name,
            "level": lvl_name,
            "reason": " + ".join(reasons),
            "isElementOverride": is_element_override,
            "isCategoryHidden": is_cat_hidden,
            "isFilterHidden": is_filter_hidden,
            "isWorksetHidden": is_ws_hidden
        })

    return hidden_items

def revit_detect_hidden_active_view(uiapp):
    doc = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    if not doc:
        raise ValueError("No hay documento activo.")
    view = doc.ActiveView
    if not view:
        raise ValueError("No hay vista activa.")

    candidates = get_candidate_hidden_elements(doc)
    items = scan_view_hidden(doc, view, candidates)

    return {
        "viewId": get_id_val(view.Id),
        "viewName": view.Name,
        "viewType": str(view.ViewType),
        "scale": view.Scale,
        "totalHidden": len(items),
        "elementOverrideCount": sum(1 for x in items if x["isElementOverride"]),
        "medicalCount": sum(1 for x in items if x["category"] == "Equipo Médico"),
        "furnitureCount": sum(1 for x in items if x["category"] == "Mobiliario"),
        "genericModelCount": sum(1 for x in items if x["category"] in ["Modelo Genérico", "Grupo de Modelo"]),
        "elements": items
    }

def revit_detect_hidden_all_views(uiapp):
    doc = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    if not doc:
        raise ValueError("No hay documento activo.")

    candidates = get_candidate_hidden_elements(doc)
    all_views = list(DB.FilteredElementCollector(doc).OfClass(DB.View).WhereElementIsNotElementType().ToElements())

    view_summaries = []
    for v in all_views:
        if v.IsTemplate or v.IsAssemblyView or not isinstance(v, (DB.ViewPlan, DB.ViewSection, DB.View3D, DB.ViewDrafting)):
            continue
        try:
            items = scan_view_hidden(doc, v, candidates)
            if items:
                view_summaries.append({
                    "viewId": get_id_val(v.Id),
                    "viewName": v.Name,
                    "viewType": str(v.ViewType),
                    "scale": v.Scale,
                    "totalHidden": len(items),
                    "elementOverrideCount": sum(1 for x in items if x["isElementOverride"]),
                    "medicalCount": sum(1 for x in items if x["category"] == "Equipo Médico"),
                    "furnitureCount": sum(1 for x in items if x["category"] == "Mobiliario"),
                    "genericModelCount": sum(1 for x in items if x["category"] in ["Modelo Genérico", "Grupo de Modelo"]),
                    "elements": items
                })
        except Exception:
            pass

    return {
        "project": doc.Title,
        "viewsWithHiddenCount": len(view_summaries),
        "totalHiddenElements": sum(s["totalHidden"] for s in view_summaries),
        "views": view_summaries
    }

def revit_unhide_elements_api(uiapp, view_id_num=None, elem_id_nums=None):
    doc = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    if not doc:
        raise ValueError("No hay documento activo.")

    view = doc.GetElement(DB.ElementId(int(view_id_num))) if view_id_num else doc.ActiveView
    if not view:
        raise ValueError("Vista no válida.")

    if elem_id_nums:
        target_ids = [DB.ElementId(int(eid)) for eid in elem_id_nums]
    else:
        candidates = get_candidate_hidden_elements(doc)
        items = scan_view_hidden(doc, view, candidates)
        target_ids = [DB.ElementId(int(x["id"])) for x in items]

    ids_to_unhide = [eid for eid in target_ids if doc.GetElement(eid) and doc.GetElement(eid).CanBeHidden(view) and doc.GetElement(eid).IsHidden(view)]

    unhidden_count = 0
    with revit.Transaction("Calidad BIM - Desocultar (MCP)"):
        if ids_to_unhide:
            try:
                view.UnhideElements(ids_to_unhide)
                unhidden_count = len(ids_to_unhide)
            except Exception as ex:
                pass

    return {
        "success": unhidden_count > 0,
        "unhiddenCount": unhidden_count,
        "message": "Se desocultaron {} elementos en la vista '{}'.".format(unhidden_count, view.Name)
    }

# ==========================================================
# HTTP REQUEST HANDLER
# ==========================================================

class McpHttpHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.lower()
        query = parse_qs(parsed.query)

        try:
            if path in ["/health", "/api/status"]:
                data = run_in_revit(revit_get_status)
                self._send_json(200, data)
            elif path == "/api/views":
                data = run_in_revit(revit_list_views)
                self._send_json(200, data)
            elif path == "/api/inspect-view":
                v_id = query.get("viewid", [None])[0]
                data = run_in_revit(lambda app: revit_inspect_view(app, v_id))
                self._send_json(200, data)
            elif path == "/api/hidden-elements/active-view":
                data = run_in_revit(revit_detect_hidden_active_view)
                self._send_json(200, data)
            elif path == "/api/hidden-elements/all-views":
                data = run_in_revit(revit_detect_hidden_all_views)
                self._send_json(200, data)
            else:
                self._send_json(404, {"error": "Ruta no encontrada", "path": path})
        except Exception as ex:
            self._send_json(500, {"error": str(ex)})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.lower()

        length = int(self.headers.get("content-length", 0))
        body_bytes = self.rfile.read(length)
        body = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}

        try:
            if path == "/api/copy-annotations":
                target_id = body.get("targetViewId")
                source_id = body.get("sourceViewId")
                data = run_in_revit(lambda app: revit_copy_annotations(app, target_id, source_id))
                self._send_json(200, data)
            elif path == "/api/hidden-elements/unhide":
                view_id = body.get("viewId")
                elem_ids = body.get("elementIds")
                data = run_in_revit(lambda app: revit_unhide_elements_api(app, view_id, elem_ids))
                self._send_json(200, data)
            else:
                self._send_json(404, {"error": "Ruta no encontrada", "path": path})
        except Exception as ex:
            self._send_json(500, {"error": str(ex)})

    def log_message(self, format, *args):
        return

def is_port_open(port=PORT):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except Exception:
        return False

def start_server():
    global _SERVER_INSTANCE, _SERVER_THREAD
    if is_port_open():
        return False

    _SERVER_INSTANCE = HTTPServer(("127.0.0.1", PORT), McpHttpHandler)
    _SERVER_THREAD = threading.Thread(target=_SERVER_INSTANCE.serve_forever)
    _SERVER_THREAD.daemon = True
    _SERVER_THREAD.start()
    return True

def main():
    if is_port_open():
        forms.alert(
            "El Servidor Puente MCP ya está ACTIVO y escuchando en:\n\n"
            "http://127.0.0.1:8050/\n\n"
            "Tu agente de IA (Antigravity, Claude, etc.) puede conectarse a Revit 2026 ahora mismo.",
            title="Calidad BIM - Servidor MCP Activo"
        )
    else:
        started = start_server()
        if started:
            forms.alert(
                "¡Servidor Puente MCP iniciado con éxito!\n\n"
                "Puerto: 8050\n"
                "URL: http://127.0.0.1:8050/\n\n"
                "Revit 2026 ahora está listo para recibir comandos automáticos desde la IA.",
                title="Calidad BIM - Servidor MCP Iniciado"
            )
        else:
            forms.alert("No se pudo iniciar el servidor en el puerto 8050.", title="Error")

if __name__ == "__main__":
    main()