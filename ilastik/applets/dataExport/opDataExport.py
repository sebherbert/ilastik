###############################################################################
#   ilastik: interactive learning and segmentation toolkit
#
#       Copyright (C) 2011-2014, the ilastik developers
#                                <team@ilastik.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# In addition, as a special exception, the copyright holders of
# ilastik give you permission to combine ilastik with applets,
# workflows and plugins which are not covered under the GNU
# General Public License.
#
# See the LICENSE file for details. License information is also available
# on the ilastik web site at:
# 		   http://ilastik.org/license.html
###############################################################################
import os
import collections
import numpy
from ndstructs import Slice5D

from ilastik.config import cfg

from lazyflow.graph import Operator, InputSlot, OutputSlot
from lazyflow.utility import PathComponents, getPathVariants, format_known_keys
from lazyflow.operators.ioOperators import OpFormattedDataExport
from lazyflow.operators.generic import OpSubRegion


class DataExportPathFormatter:
    def __init__(self, *, dataset_info, working_dir, result_type=None):
        self._result_type = result_type
        self._working_dir = working_dir
        self._dataset_info = dataset_info

    def format_path(self, path_template: str) -> str:
        dataset_dir = str(self._dataset_info.default_output_dir)
        abs_dataset_dir, _ = getPathVariants(dataset_dir, self._working_dir)

        nickname = self._dataset_info.nickname.replace("*", "")
        if os.path.pathsep in nickname:
            nickname = PathComponents(nickname.split(os.path.pathsep)[0]).fileNameBase

        known_keys = {"dataset_dir": abs_dataset_dir, "nickname": nickname}

        if self._result_type:
            known_keys["result_type"] = self._result_type

        return format_known_keys(path_template, known_keys)


class OpDataExport(Operator):
    """
    Top-level operator for the export applet.
    Mostly a simple wrapper for OpProcessedDataExport, but with extra formatting of the export path based on lane attributes.
    """

    TransactionSlot = InputSlot()  # To apply all settings in one 'transaction',
    # disconnect this slot and reconnect it when all slots are ready

    RawData = InputSlot(optional=True)  # Display only
    FormattedRawData = OutputSlot()  # OUTPUT - for overlaying the transformed raw data with the export data.

    # The dataset info for the original dataset (raw data)
    RawDatasetInfo = InputSlot()
    WorkingDirectory = (
        InputSlot()
    )  # Non-absolute paths are relative to this directory.  If not provided, paths must be absolute.

    Inputs = InputSlot(level=1)  # The exportable slots (should all be of the same shape, except for channel)
    InputSelection = InputSlot(value=0)
    SelectionNames = InputSlot()  # A list of names corresponding to the exportable inputs

    # Subregion params
    RegionStart = InputSlot(optional=True)
    RegionStop = InputSlot(optional=True)

    # Normalization params
    InputMin = InputSlot(optional=True)
    InputMax = InputSlot(optional=True)
    ExportMin = InputSlot(optional=True)
    ExportMax = InputSlot(optional=True)

    ExportDtype = InputSlot(optional=True)
    OutputAxisOrder = InputSlot(optional=True)

    # File settings
    OutputFilenameFormat = InputSlot(
        value=cfg["ilastik"]["output_filename_format"]
    )  # A format string allowing {dataset_dir} {nickname}, {roi}, {x_start}, {x_stop}, etc.
    OutputInternalPath = InputSlot(value="exported_data")
    OutputFormat = InputSlot(value="hdf5")

    # Only export csv/HDF5 table (don't export volume)
    TableOnlyName = InputSlot(value="Table-Only")
    TableOnly = InputSlot(value=False)

    ExportPath = OutputSlot()  # Location of the saved file after export is complete.

    ConvertedImage = OutputSlot()  # Cropped image, not yet re-ordered (useful for guis)
    ImageToExport = OutputSlot()  # The image that will be exported

    Dirty = OutputSlot()  # Whether or not the result currently matches what's on disk
    FormatSelectionErrorMsg = OutputSlot()

    ALL_FORMATS = OpFormattedDataExport.ALL_FORMATS

    ####
    # Simplified block diagram for actual export data and 'live preview' display:
    #
    #                            --> ExportPath
    #                           /
    # Input -> opFormattedExport --> ImageToExport (live preview)
    #                          |\
    #                          \ --> ConvertedImage
    #                           \
    #                            --> FormatSeletionIsValid

    ####
    # Simplified block diagram for Raw data display:
    #
    # RegionStart --
    #               \
    # RegionStop --> OpRawSubRegionHelper.RawStart -
    #               /                    .RawStop --\
    #              /                                 \
    # RawData ---->---------------------------------> opFormatRaw --> FormattedRawData

    ####
    def __init__(self, *args, **kwargs):
        super(OpDataExport, self).__init__(*args, **kwargs)

        self._opFormattedExport = OpFormattedDataExport(parent=self)
        opFormattedExport = self._opFormattedExport

        # Forward almost all inputs to the 'real' exporter
        opFormattedExport.TransactionSlot.connect(self.TransactionSlot)
        opFormattedExport.RegionStart.connect(self.RegionStart)
        opFormattedExport.RegionStop.connect(self.RegionStop)
        opFormattedExport.InputMin.connect(self.InputMin)
        opFormattedExport.InputMax.connect(self.InputMax)
        opFormattedExport.ExportMin.connect(self.ExportMin)
        opFormattedExport.ExportMax.connect(self.ExportMax)
        opFormattedExport.ExportDtype.connect(self.ExportDtype)
        opFormattedExport.OutputAxisOrder.connect(self.OutputAxisOrder)
        opFormattedExport.OutputFormat.connect(self.OutputFormat)

        self.ConvertedImage.connect(opFormattedExport.ConvertedImage)
        self.ImageToExport.connect(opFormattedExport.ImageToExport)
        self.ExportPath.connect(opFormattedExport.ExportPath)
        self.FormatSelectionErrorMsg.connect(opFormattedExport.FormatSelectionErrorMsg)
        self.progressSignal = opFormattedExport.progressSignal

        self.Dirty.setValue(True)  # Default to Dirty

        # We don't export the raw data, but we connect it to it's own op
        #  so it can be displayed alongside the data to export in the same viewer.
        # This keeps axis order, shape, etc. in sync with the displayed export data.
        # Note that we must not modify the channels of the raw data, so it gets passed through a helper.
        opHelper = OpRawSubRegionHelper(parent=self)
        opHelper.RawImage.connect(self.RawData)
        opHelper.ExportStart.connect(self.RegionStart)
        opHelper.ExportStop.connect(self.RegionStop)

        opFormatRaw = OpFormattedDataExport(parent=self)
        opFormatRaw.TransactionSlot.connect(self.TransactionSlot)
        opFormatRaw.Input.connect(self.RawData)
        opFormatRaw.RegionStart.connect(opHelper.RawStart)
        opFormatRaw.RegionStop.connect(opHelper.RawStop)
        # Don't normalize the raw data.
        # opFormatRaw.InputMin.connect( self.InputMin )
        # opFormatRaw.InputMax.connect( self.InputMax )
        # opFormatRaw.ExportMin.connect( self.ExportMin )
        # opFormatRaw.ExportMax.connect( self.ExportMax )
        # opFormatRaw.ExportDtype.connect( self.ExportDtype )
        opFormatRaw.OutputAxisOrder.connect(self.OutputAxisOrder)
        opFormatRaw.OutputFormat.connect(self.OutputFormat)
        self._opFormatRaw = opFormatRaw
        self.FormattedRawData.connect(opFormatRaw.ImageToExport)

    def setupOutputs(self):
        # FIXME: If RawData becomes unready() at the same time as RawDatasetInfo(), then
        #          we have no guarantees about which one will trigger setupOutputs() first.
        #        It is therefore possible for 'RawDatasetInfo' to appear ready() to us,
        #          even though it's upstream partner is UNready.  We are about to get the
        #          unready() notification, but it will come too late to prevent our
        #          setupOutputs method from being called.
        #        Without proper graph setup transaction semantics, we have to use this
        #          hack as a workaround.
        try:
            rawInfo = self.RawDatasetInfo.value
        except:
            for oslot in list(self.outputs.values()):
                if oslot.upstream_slot is None:
                    oslot.meta.NOTREADY = True
            return

        selection_index = self.InputSelection.value
        if not self.Inputs[selection_index].ready():
            for oslot in list(self.outputs.values()):
                if oslot.upstream_slot is None:
                    oslot.meta.NOTREADY = True
            return

        self._opFormattedExport.Input.connect(self.Inputs[selection_index])
        result_types = self.SelectionNames.value

        path_formatter = DataExportPathFormatter(
            dataset_info=rawInfo, working_dir=self.WorkingDirectory.value, result_type=result_types[selection_index]
        )

        self._opFormattedExport.TransactionSlot.disconnect()

        # Blank the internal path while we manipulate the external path
        #  to avoid invalid intermediate states of ExportPath
        self._opFormattedExport.OutputInternalPath.setValue("")

        # use partial formatting to fill in non-coordinate name fields
        name_format = self.OutputFilenameFormat.value
        partially_formatted_name = path_formatter.format_path(name_format)

        # Convert to absolute path before configuring the internal op
        abs_path, _ = getPathVariants(partially_formatted_name, self.WorkingDirectory.value)
        self._opFormattedExport.OutputFilenameFormat.setValue(abs_path)

        # use partial formatting on the internal dataset name, too
        internal_dataset_format = self.OutputInternalPath.value
        partially_formatted_dataset_name = path_formatter.format_path(internal_dataset_format)
        self._opFormattedExport.OutputInternalPath.setValue(partially_formatted_dataset_name)

        # Re-connect to finish the 'transaction'
        self._opFormattedExport.TransactionSlot.connect(self.TransactionSlot)

    def execute(self, slot, subindex, roi, result):
        assert False, "Shouldn't get here"

    def propagateDirty(self, slot, subindex, roi):
        # Out input data changed, so we have work to do when we get executed.
        self.Dirty.setValue(True)

    def run_export(self):
        # If Table-Only is disabled or we're not dirty, we don't have to do anything.
        if not self.TableOnly.value and self.Dirty.value:
            self._opFormattedExport.run_export()
            self.Dirty.setValue(False)

    def run_export_to_array(self):
        # This function can be used to export the results to an in-memory array, instead of to disk
        # (Typically used from pure-python clients in batch mode.)
        return self._opFormattedExport.run_export_to_array()

    def run_distributed_export(self, block_roi: Slice5D):
        return self._opFormattedExport.run_distributed_export(block_roi)


class OpRawSubRegionHelper(Operator):
    """
    We display the raw data underneath the export data.
    To do that, we need to show the SAME subregion of the raw data that the user selected to export.
    However, it's possible that the exported data has a different number of channels than the raw data has.
    Therefore, the subregion for the raw layer should be the same in all dimensions EXCEPT for the number of channels.

    This simple helper operator produces the correct subregion settings to be used with the raw data formatting operator.
    """

    RawImage = InputSlot()
    ExportStart = InputSlot(optional=True)
    ExportStop = InputSlot(optional=True)

    RawStart = OutputSlot()
    RawStop = OutputSlot()

    def setupOutputs(self):
        tagged_shape = self.RawImage.meta.getTaggedShape()
        if self.ExportStart.ready():
            tagged_start = collections.OrderedDict(list(zip(self.RawImage.meta.getAxisKeys(), self.ExportStart.value)))
            tagged_start["c"] = 0
            self.RawStart.setValue(list(tagged_start.values()))
        else:
            self.RawStart.meta.NOTREADY = True

        if self.ExportStop.ready():
            tagged_stop = collections.OrderedDict(list(zip(self.RawImage.meta.getAxisKeys(), self.ExportStop.value)))
            tagged_stop["c"] = tagged_shape["c"]
            self.RawStop.setValue(list(tagged_stop.values()))
        else:
            self.RawStop.meta.NOTREADY = True

    def execute(self, slot, subindex, roi, result):
        assert False, "Shouldn't get here"

    def propagateDirty(self, slot, subindex, roi):
        pass  # No need to do anything here.


def get_model_op(wrappedOp):
    """
    Create a "model operator" that the gui can use.
    The model op is a single (non-wrapped) export operator that the
    gui will manipulate while the user plays around with the export
    settings.  When the user is finished, the model op slot settings can
    be copied over to the 'real' (wrapped) operator slots.
    """
    if len(wrappedOp) == 0:
        return None, None

    # These are the slots the export settings gui will manipulate.
    setting_slots = [
        wrappedOp.RegionStart,
        wrappedOp.RegionStop,
        wrappedOp.InputMin,
        wrappedOp.InputMax,
        wrappedOp.ExportMin,
        wrappedOp.ExportMax,
        wrappedOp.ExportDtype,
        wrappedOp.OutputAxisOrder,
        wrappedOp.OutputFilenameFormat,
        wrappedOp.OutputInternalPath,
        wrappedOp.OutputFormat,
    ]

    # Use an instance of OpFormattedDataExport, since has the important slots and no others.
    model_op = OpFormattedDataExport(parent=wrappedOp.parent)
    for slot in setting_slots:
        model_inslot = getattr(model_op, slot.name)
        if slot.ready():
            model_inslot.setValue(slot.value)

    # Choose a roi that can apply to all images in the original operator
    shape = None
    axes = None
    for multislot in wrappedOp.Inputs:
        slot = multislot[wrappedOp.InputSelection.value]
        if slot.ready():
            if shape is None:
                shape = slot.meta.shape
                axes = slot.meta.getAxisKeys()
                dtype = slot.meta.dtype
            else:
                assert slot.meta.getAxisKeys() == axes, "Can't export multiple slots with different axes."
                assert slot.meta.dtype == dtype
                shape = numpy.minimum(slot.meta.shape, shape)

    # If NO slots were ready, then we can't do anything here.
    if shape is None:
        return None, None

    # Must provide a 'ready' slot for the gui
    # Use a subregion operator to provide a slot with the meta data we chose.
    opSubRegion = OpSubRegion(parent=wrappedOp.parent)
    opSubRegion.Roi.setValue([(0,) * len(shape), tuple(shape)])
    opSubRegion.Input.connect(slot)

    # (The actual contents of this slot are not important to the settings gui.
    #  It only cares about the metadata.)
    model_op.Input.connect(opSubRegion.Output)

    return model_op, opSubRegion  # We return the subregion op, too, so the caller can clean it up.
