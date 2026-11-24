# -*- coding: utf-8 -*-
import os
import re
import zipfile
from datetime import datetime
from pyrevit import revit, DB, forms, script

doc = revit.doc


def col_letter(col_idx):
    """Convierte indice de columna 1-basado (1=A, 2=B...) a letra de Excel."""
    result = ""
    while col_idx > 0:
        col_idx, rem = divmod(col_idx - 1, 26)
        result = chr(65 + rem) + result
    return result


def xml_escape(text):
    if text is None:
        return ""
    s = unicode(text) if not isinstance(text, unicode) else text
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&apos;")
    )


def is_number_like(val):
    if val is None:
        return False
    s = str(val).strip()
    if not s:
        return False
    return bool(re.match(r"^-?\d+(\.\d+)?$", s))


def sanitize_windows_filename(name):
    """Mantiene el nombre exacto de la tabla y solo quita caracteres prohibidos en Windows."""
    return re.sub(r'[\\/:*?"<>|]', "", name).strip()


def write_styled_xlsx(filepath, sheet_name, subtitulo, titulo_tabla, headers, rows_data):
    """
    Genera un archivo .xlsx valido y compatible con Excel:
    - Fila 2 (Col B): subtitulo ruta/fecha en cursiva
    - Fila 3 (Col B): titulo de tabla en negrita (16pt)
    - Fila 5 (desde Col B): encabezados en negrita + sombreado gris (#E7E6E6) + bordes
    - Fila 7+ (desde Col B): datos con bordes en celdas con data y autoajuste de ancho
    """
    safe_sheet = re.sub(r"[\[\]\*:/\?\\]", "_", sheet_name or "Revit Schedule")[:31]
    num_cols = len(headers)

    col_widths = {1: 3.0}
    for idx_c, h_text in enumerate(headers):
        excel_col = 2 + idx_c
        max_len = len(unicode(h_text)) if h_text else 0
        for r_vals in rows_data:
            cell_val = r_vals[idx_c] if idx_c < len(r_vals) else ""
            val_len = len(unicode(cell_val)) if cell_val else 0
            if val_len > max_len:
                max_len = val_len
        col_widths[excel_col] = max(12.0, min(65.0, max_len * 1.15 + 4.0))

    sheet_rows_xml = []

    sheet_rows_xml.append(
        '<row r="2"><c r="B2" s="1" t="inlineStr"><is><t xml:space="preserve">{txt}</t></is></c></row>'.format(
            txt=xml_escape(subtitulo)
        )
    )

    sheet_rows_xml.append(
        '<row r="3"><c r="B3" s="2" t="inlineStr"><is><t xml:space="preserve">{txt}</t></is></c></row>'.format(
            txt=xml_escape(titulo_tabla)
        )
    )

    header_cells = []
    for idx_c, h_text in enumerate(headers):
        col_str = col_letter(2 + idx_c)
        cell_ref = "{}5".format(col_str)
        header_cells.append(
            '<c r="{ref}" s="3" t="inlineStr"><is><t xml:space="preserve">{txt}</t></is></c>'.format(
                ref=cell_ref, txt=xml_escape(h_text)
            )
        )
    sheet_rows_xml.append('<row r="5">{}</row>'.format("".join(header_cells)))

    for idx_r, r_vals in enumerate(rows_data):
        excel_row = 7 + idx_r
        data_cells = []
        has_any_data = False
        for idx_c in range(num_cols):
            val = r_vals[idx_c] if idx_c < len(r_vals) else ""
            val_str = unicode(val).strip() if val is not None else ""
            if val_str != "":
                has_any_data = True
                col_str = col_letter(2 + idx_c)
                cell_ref = "{}{}".format(col_str, excel_row)
                if is_number_like(val_str):
                    data_cells.append(
                        '<c r="{ref}" s="5"><v>{num}</v></c>'.format(
                            ref=cell_ref, num=val_str
                        )
                    )
                else:
                    data_cells.append(
                        '<c r="{ref}" s="4" t="inlineStr"><is><t xml:space="preserve">{txt}</t></is></c>'.format(
                            ref=cell_ref, txt=xml_escape(val_str)
                        )
                    )
        if has_any_data and data_cells:
            sheet_rows_xml.append(
                '<row r="{r}">{cells}</row>'.format(r=excel_row, cells="".join(data_cells))
            )

    cols_xml_parts = []
    for c_idx in sorted(col_widths.keys()):
        w = col_widths[c_idx]
        cols_xml_parts.append(
            '<col min="{c}" max="{c}" width="{w:.2f}" customWidth="1"/>'.format(c=c_idx, w=w)
        )
    cols_xml = "<cols>{}</cols>".format("".join(cols_xml_parts))

    sheet_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews>
    <sheetView showGridLines="1" workbookViewId="0"/>
  </sheetViews>
  {cols}
  <sheetData>
    {rows}
  </sheetData>
</worksheet>""".format(cols=cols_xml, rows="\n    ".join(sheet_rows_xml))

    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="4">
    <font><name val="Calibri"/><sz val="9"/></font>
    <font><name val="Calibri"/><i/><color rgb="FF595959"/><sz val="10"/></font>
    <font><name val="Calibri"/><b/><color rgb="FF000000"/><sz val="16"/></font>
    <font><name val="Calibri"/><b/><color rgb="FF000000"/><sz val="9"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE7E6E6"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border>
      <left style="thin"><color rgb="FFBFBFBF"/></left>
      <right style="thin"><color rgb="FFBFBFBF"/></right>
      <top style="thin"><color rgb="FFBFBFBF"/></top>
      <bottom style="thin"><color rgb="FFBFBFBF"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="6">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
    <xf numFmtId="0" fontId="3" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>
    <xf numFmtId="1" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyNumberFormat="1"/>
  </cellXfs>
  <cellStyles count="1">
    <cellStyle name="Normal" xfId="0" builtinId="0"/>
  </cellStyles>
</styleSheet>"""

    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

    rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="{sname}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""".format(sname=xml_escape(safe_sheet))

    wb_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

    with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml.encode("utf-8"))
        zf.writestr("_rels/.rels", rels_xml.encode("utf-8"))
        zf.writestr("xl/workbook.xml", workbook_xml.encode("utf-8"))
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels_xml.encode("utf-8"))
        zf.writestr("xl/styles.xml", styles_xml.encode("utf-8"))
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml.encode("utf-8"))


try:
    schedules = []
    mapa_schedules = {}

    for vs in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        if vs.IsTitleblockRevisionSchedule or vs.IsInternalKeynoteSchedule:
            continue
        if not vs.Name or vs.Name.startswith("<"):
            continue
        schedules.append(vs.Name)
        mapa_schedules[vs.Name] = vs

    schedules.sort()

    if not schedules:
        forms.alert(
            "No se encontraron tablas de planificación válidas en este modelo.",
            title="Exportar Tablas UPSS"
        )
        script.exit()

    seleccionados = forms.SelectFromList.show(
        schedules,
        title="Seleccionar Tablas de Metrado UPSS",
        button_name="Exportar a Excel (.xlsx)",
        multiselect=True,
        width=650,
        height=500
    )

    if not seleccionados:
        script.exit()

    carpeta_destino = forms.pick_folder(
        title="Selecciona la carpeta donde guardar los archivos Excel (.xlsx)"
    )
    if not carpeta_destino:
        script.exit()

    model_info = doc.Title + ".rvt"
    try:
        if doc.IsModelInCloud:
            cloud_path = doc.GetCloudModelPath()
            user_path = DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(cloud_path)
            if user_path:
                model_info = user_path
        elif doc.PathName:
            model_info = doc.PathName
    except Exception:
        pass

    fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    linea_modelo = "{} - {}".format(model_info, fecha_hoy)

    exportados = 0

    for nombre_tabla in seleccionados:
        vs = mapa_schedules[nombre_tabla]
        table_data = vs.GetTableData()
        body = table_data.GetSectionData(DB.SectionType.Body)

        n_rows = body.NumberOfRows
        n_cols = body.NumberOfColumns

        if n_rows < 1 or n_cols < 1:
            continue

        filas = []
        for r in range(n_rows):
            fila = []
            for c in range(n_cols):
                txt = vs.GetCellText(DB.SectionType.Body, r, c)
                fila.append(txt.strip() if txt else "")
            if any(fila):
                filas.append(fila)

        if not filas:
            continue

        if len(filas) > 1 and (len(filas[0]) == 1 or filas[0][0].upper() == nombre_tabla.upper()):
            encabezados = filas[1]
            datos = filas[2:]
        else:
            encabezados = filas[0]
            datos = filas[1:]

        nombre_archivo = sanitize_windows_filename(nombre_tabla)
        ruta_xlsx = os.path.join(carpeta_destino, nombre_archivo + ".xlsx")

        write_styled_xlsx(
            filepath=ruta_xlsx,
            sheet_name="Revit Schedule",
            subtitulo=linea_modelo,
            titulo_tabla=nombre_tabla,
            headers=encabezados,
            rows_data=datos
        )
        exportados += 1

    forms.alert(
        "Se exportaron {} archivo(s) Excel (.xlsx) listos para abrir en:\n\n{}".format(
            exportados, carpeta_destino
        ),
        title="Exportación Completada"
    )

except Exception as ex:
    forms.alert(
        "Ocurrió un error al exportar:\n\n{}".format(str(ex)),
        title="Error - Exportar Tablas UPSS"
    )