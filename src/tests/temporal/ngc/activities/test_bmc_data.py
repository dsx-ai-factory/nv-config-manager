# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from typing import Any

from nv_config_manager.temporal.client.device import DeviceArpTable
from nv_config_manager.temporal.client.redfish import (
    RedfishDpu,
    RedfishDpuPort,
    RedfishVendor,
)

LENOVO_REDFISH_BASE = {
    "UpdateService": {"@odata.id": "/redfish/v1/UpdateService"},
    "Vendor": "Lenovo",
    "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
    "Managers": {"@odata.id": "/redfish/v1/Managers"},
    "AccountService": {"@odata.id": "/redfish/v1/AccountService"},
    "@odata.etag": '"aeb04838fdcf33b6795ab"',
    "LicenseService": {"@odata.id": "/redfish/v1/LicenseService"},
    "JsonSchemas": {"@odata.id": "/redfish/v1/JsonSchemas"},
    "ProtocolFeaturesSupported": {
        "ExpandQuery": {
            "ExpandAll": True,
            "MaxLevels": 2,
            "Links": True,
            "Levels": True,
            "NoLinks": True,
        },
        "MultipleHTTPRequests": True,
        "ExcerptQuery": True,
        "FilterQuery": True,
        "SelectQuery": True,
        "DeepOperations": {"DeepPOST": True, "MaxLevels": 2, "DeepPATCH": True},
        "OnlyMemberQuery": True,
    },
    "CertificateService": {"@odata.id": "/redfish/v1/CertificateService"},
    "JobService": {"@odata.id": "/redfish/v1/JobService"},
    "Name": "Root Service",
    "@odata.type": "#ServiceRoot.v1_14_0.ServiceRoot",
    "@odata.id": "/redfish/v1/",
    "Links": {"Sessions": {"@odata.id": "/redfish/v1/SessionService/Sessions"}},
    "Description": (
        "This resource is used to represent a service root for a Redfish implementation."
    ),
    "@odata.context": "/redfish/v1/$metadata#ServiceRoot.ServiceRoot",
    "SessionService": {"@odata.id": "/redfish/v1/SessionService"},
    "UUID": "11448C02-27FF-11EF-95CD-3A7C768D6F15",
    "Id": "RootService",
    "RedfishVersion": "1.16.0",
    "EventService": {"@odata.id": "/redfish/v1/EventService"},
    "Tasks": {"@odata.id": "/redfish/v1/TaskService"},
    "TelemetryService": {"@odata.id": "/redfish/v1/TelemetryService"},
    "ServiceIdentification": "",
    "Registries": {"@odata.id": "/redfish/v1/Registries"},
    "Systems": {"@odata.id": "/redfish/v1/Systems"},
}

LENOVO_PASSWORD_RESPONSE = {
    "Id": "1",
    "Name": "User1",
    "@odata.type": "#ManagerAccount.v1_9_0.ManagerAccount",
    "@odata.id": "/redfish/v1/AccountService/Accounts/1",
    "Oem": {
        "Lenovo": {
            "@odata.type": "#LenovoManagerAccount.v1_0_0.LenovoManagerAccount",
            "NoPasswordChangeInterval": False,
            "SSHPublicKey": [None, None, None, None],
        }
    },
    "Links": {"Role": {"@odata.id": "/redfish/v1/AccountService/Roles/Administrator"}},
    "Locked": False,
    "HostBootstrapAccount": False,
    "AccountTypes@Redfish.AllowableValues": [
        "WebUI",
        "Redfish",
        "ManagerConsole",
        "IPMI",
        "SNMP",
    ],
    "SNMP": {
        "EncryptionProtocol": "CFB128_AES128",
        "EncryptionKey": None,
        "EncryptionKeySet": False,
        "AuthenticationProtocol": "HMAC_SHA96",
    },
    "RoleId": "Administrator",
    "PasswordChangeRequired": False,
    "AccountTypes": ["WebUI", "Redfish", "ManagerConsole"],
    "Enabled": True,
    "PasswordExpiration": "2024-09-17T18:18:07Z",
    "Description": (
        "This resource is used to represent an account for the manager for a Redfish"
        " implementation."
    ),
    "Keys": {"@odata.id": "/redfish/v1/AccountService/Accounts/1/Keys"},
    "@odata.etag": '"878c0f5b9c502434bb6"',
    "@odata.context": "/redfish/v1/$metadata#ManagerAccount.ManagerAccount",
    "UserName": "USERID",
    "Password": None,
}


LENOVO_UNAUTHORIZED_RESPONSE = {
    "error": {
        "code": "Base.1.14.GeneralError",
        "@Message.ExtendedInfo": [
            {
                "@odata.type": "#Message.v1_1_2.Message",
                "Resolution": (
                    "Attempt to ensure that the URI is correct and that the service has the"
                    " appropriate credentials."
                ),
                "Message": (
                    "While attempting to establish a connection to"
                    " '/redfish/v1/AccountService/Accounts/1', the service denied access."
                ),
                "MessageArgs": ["/redfish/v1/AccountService/Accounts/1"],
                "MessageId": "Base.1.14.AccessDenied",
                "MessageSeverity": "Critical",
            }
        ],
        "message": ("A general error has occurred. See ExtendedInfo for more information."),
    }
}


LENOVO_SYSTEM_INFO = {
    "LogServices": {"@odata.id": "/redfish/v1/Systems/1/LogServices"},
    "HostWatchdogTimer": {
        "WarningAction@Redfish.AllowableValues": ["None"],
        "FunctionEnabled": False,
        "WarningAction": "None",
        "TimeoutAction": "PowerCycle",
        "Status": {"State": "Disabled"},
        "TimeoutAction@Redfish.AllowableValues": ["PowerCycle"],
    },
    "VirtualMedia": {"@odata.id": "/redfish/v1/Systems/1/VirtualMedia"},
    "HostName": "XCC-7D9E-JZ00268C",
    "Id": "1",
    "@odata.etag": '"2cdae2a88385d3596d2c34"',
    "Links": {
        "CooledBy": [
            {"@odata.id": "/redfish/v1/Chassis/1/Thermal#/Fans/0"},
            {"@odata.id": "/redfish/v1/Chassis/1/Thermal#/Fans/1"},
            {"@odata.id": "/redfish/v1/Chassis/1/Thermal#/Fans/2"},
            {"@odata.id": "/redfish/v1/Chassis/1/Thermal#/Fans/3"},
        ],
        "Chassis": [{"@odata.id": "/redfish/v1/Chassis/1"}],
        "PoweredBy": [
            {"@odata.id": "/redfish/v1/Chassis/1/Power#/PowerSupplies/0"},
            {"@odata.id": "/redfish/v1/Chassis/1/Power#/PowerSupplies/1"},
        ],
        "ManagedBy": [{"@odata.id": "/redfish/v1/Managers/1"}],
    },
    "EthernetInterfaces": {"@odata.id": "/redfish/v1/Systems/1/EthernetInterfaces"},
    "Description": "This resource is used to represent a computing system for a Redfish implementation.",
    "Boot": {
        "BootSourceOverrideTarget@Redfish.AllowableValues": [
            "None",
            "Pxe",
            "Cd",
            "Usb",
            "Hdd",
            "BiosSetup",
            "Diags",
            "UefiTarget",
        ],
        "AutomaticRetryAttempts": 50,
        "BootSourceOverrideEnabled@Redfish.AllowableValues": ["Once", "Disabled"],
        "BootOrderPropertySelection": "BootOrder",
        "BootOrder": ["Boot0001", "Boot0002", "Boot0003"],
        "BootSourceOverrideTarget": "None",
        "BootSourceOverrideMode": "UEFI",
        "UefiTargetBootSourceOverride": None,
        "BootSourceOverrideEnabled": "Disabled",
        "AutomaticRetryConfig": "RetryAttempts",
        "BootOptions": {"@odata.id": "/redfish/v1/Systems/1/BootOptions"},
    },
    "Actions": {
        "Oem": {
            "#LenovoComputerSystem.BootToBIOSSetup": {
                "title": "BootToBIOSSetup",
                "target": "/redfish/v1/Systems/1/Actions/Oem/LenovoComputerSystem.BootToBIOSSetup",
            },
            "#LenovoComputerSystem.CustomizedReset": {
                "target": "/redfish/v1/Systems/1/Actions/Oem/LenovoComputerSystem.CustomizedReset",
                "title": "CustomizedReset",
                "ResetType@Redfish.AllowableValues": ["On"],
            },
            "#LenovoComputerSystem.SystemReset": {
                "target": "/redfish/v1/Systems/1/Actions/Oem/LenovoComputerSystem.SystemReset",
                "title": "SystemReset",
                "ResetType@Redfish.AllowableValues": ["ACPowerCycle"],
            },
            "#LenovoComputerSystem.RemoteClearCMOS": {
                "title": "RemoteClearCMOS",
                "target": "/redfish/v1/Systems/1/Actions/Oem/LenovoComputerSystem.RemoteClearCMOS",
            },
        },
        "#ComputerSystem.Reset": {
            "@Redfish.ActionInfo": "/redfish/v1/Systems/1/ResetActionInfo",
            "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            "title": "Reset",
            "ResetType@Redfish.AllowableValues": [
                "On",
                "Nmi",
                "GracefulShutdown",
                "GracefulRestart",
                "ForceOn",
                "ForceOff",
                "ForceRestart",
            ],
        },
    },
    "@odata.context": "/redfish/v1/$metadata#ComputerSystem.ComputerSystem",
    "ProcessorSummary": {
        "Count": 1,
        "Metrics": {"@odata.id": "/redfish/v1/Systems/1/ProcessorSummary/ProcessorMetrics"},
        "LogicalProcessorCount": 64,
        "Model": "AMD EPYC 9334 32-Core Processor",
        "Status": {"Health": "OK", "HealthRollup": "OK", "State": "Enabled"},
    },
    "Model": "ThinkSystem SR655 V3",
    "UUID": "11448c02-27ff-11ef-95cd-3a7c768d6f15",
    "SKU": "7D9ECTO1WW",
    "@Redfish.Settings": {
        "@odata.type": "#Settings.v1_3_0.Settings",
        "Time": None,
        "SettingsObject": {"@odata.id": "/redfish/v1/Systems/1/Pending"},
        "Messages": [],
        "SupportedApplyTimes": ["OnReset"],
    },
    "PowerState": "Off",
    "KeyManagement": {
        "KMIPCertificates": {"@odata.id": "/redfish/v1/Systems/1/KeyManagement/KMIPCertificates"},
        "KMIPServers": [
            {"Address": None, "Port": 5696},
            {"Address": None, "Port": 5696},
            {"Address": None, "Port": 5696},
            {"Address": None, "Port": 5696},
        ],
    },
    "BiosVersion": "KAE118M",
    "SubModel": "7D9E",
    "Memory": {"@odata.id": "/redfish/v1/Systems/1/Memory"},
    "Processors": {"@odata.id": "/redfish/v1/Systems/1/Processors"},
    "PCIeFunctions@odata.count": 6,
    "PCIeDevices@odata.count": 3,
    "Bios": {"@odata.id": "/redfish/v1/Systems/1/Bios"},
    "PCIeFunctions": [
        {"@odata.id": "/redfish/v1/Chassis/1/PCIeDevices/ob_2/PCIeFunctions/ob_2.00"},
        {"@odata.id": "/redfish/v1/Chassis/1/PCIeDevices/slot_2/PCIeFunctions/slot_2.00"},
        {"@odata.id": "/redfish/v1/Chassis/1/PCIeDevices/ob_2/PCIeFunctions/ob_2.01"},
        {"@odata.id": "/redfish/v1/Chassis/1/PCIeDevices/slot_2/PCIeFunctions/slot_2.02"},
        {"@odata.id": "/redfish/v1/Chassis/1/PCIeDevices/slot_2/PCIeFunctions/slot_2.01"},
        {"@odata.id": "/redfish/v1/Chassis/1/PCIeDevices/ob_1/PCIeFunctions/ob_1.00"},
    ],
    "IndicatorLED": "Off",
    "Manufacturer": "Lenovo",
    "PartNumber": None,
    "NetworkInterfaces": {"@odata.id": "/redfish/v1/Systems/1/NetworkInterfaces"},
    "SecureBoot": {"@odata.id": "/redfish/v1/Systems/1/SecureBoot"},
    "SerialNumber": "JZ00268C",
    "MemorySummary": {
        "Status": {"Health": "OK", "HealthRollup": "OK", "State": "Enabled"},
        "TotalSystemMemoryGiB": 384,
        "Metrics": {"@odata.id": "/redfish/v1/Systems/1/MemorySummary/MemoryMetrics"},
    },
    "Storage": {"@odata.id": "/redfish/v1/Systems/1/Storage"},
    "PCIeDevices": [
        {"@odata.id": "/redfish/v1/Chassis/1/PCIeDevices/ob_2"},
        {"@odata.id": "/redfish/v1/Chassis/1/PCIeDevices/slot_2"},
        {"@odata.id": "/redfish/v1/Chassis/1/PCIeDevices/ob_1"},
    ],
    "@odata.type": "#ComputerSystem.v1_19_0.ComputerSystem",
    "Status": {"Health": "Warning", "HealthRollup": "Warning", "State": "Enabled"},
    "@odata.id": "/redfish/v1/Systems/1",
    "AssetTag": "",
    "Oem": {
        "Lenovo": {
            "HistorySysPerf": {"@odata.id": "/redfish/v1/Systems/1/Oem/Lenovo/HistorySysPerf"},
            "ScheduledPowerActions": {
                "@odata.id": "/redfish/v1/Systems/1/Oem/Lenovo/ScheduledPowerActions"
            },
            "TotalPowerOnHours": 11,
            "LXPMDiagFlag": None,
            "SSDWearThreshold": 8,
            "BootSettings@Redfish.Deprecated": "The property is deprecated. Please use BootOrder instead.",
            "TPMSettings": {
                "AssertRPP": False,
                "AssertDurationMins": 30,
                "EnableRPP": True,
            },
            "USBManagementPortAssignment": {
                "PortSwitchingTo": "BMC",
                "FPMode": "Shared",
                "InactivityTimeoutMins": 5,
                "IDButton": "On",
            },
            "@odata.type": "#LenovoComputerSystem.v1_0_0.LenovoSystemProperties",
            "Metrics": {"@odata.id": "/redfish/v1/Systems/1/Oem/Lenovo/Metrics"},
            "SystemStatus": "SystemPowerOff_StateUnknown",
            "SecureBootOverKCSSettings": {
                "AllowDisableSecureBootOverKCS": False,
                "AllowEnableSecureBootOverKCS": True,
            },
            "Sensors": {"@odata.id": "/redfish/v1/Chassis/1/Sensors"},
            "BootSettings": {"@odata.id": "/redfish/v1/Systems/1/Oem/Lenovo/BootSettings"},
            "NumberOfReboots": 2,
        }
    },
    "SystemType": "Physical",
    "Name": "ComputerSystem",
}


LENOVO_NETWORK_ADAPTERS = {
    "@odata.type": "#NetworkAdapterCollection.NetworkAdapterCollection",
    "Members@odata.count": 1,
    "@odata.context": "/redfish/v1/$metadata#NetworkAdapterCollection.NetworkAdapterCollection",
    "Members": [{"@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/slot-2"}],
    "Description": "A collection of NetworkAdapter resource instances.",
    "@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters",
    "Name": "NetworkAdapterCollection",
    "@odata.etag": '"3104b17175de25a7122"',
}


LENOVO_NETWORK_ADAPTER_DETAILS = {
    "slot-2": {
        "SerialNumber": "MT2403XZ04GN",
        "NetworkDeviceFunctions": {
            "@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/slot-2/NetworkDeviceFunctions"
        },
        "Id": "slot-2",
        "Name": "NetworkAdapter",
        "@odata.type": "#NetworkAdapter.v1_9_0.NetworkAdapter",
        "@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/slot-2",
        "Description": "A NetworkAdapter represents the physical network adapter capable of connecting to a computer network.",
        "Oem": {
            "Lenovo": {
                "@odata.type": "#LenovoNetworkAdapter.v1_0_0.LenovoNetworkAdapter",
                "UUID": "",
            }
        },
        "@odata.context": "/redfish/v1/$metadata#NetworkAdapter.NetworkAdapter",
        "PartNumber": "900-9D3B6-00CV-A",
        "Status": {"State": "Enabled", "Health": "OK"},
        "Ports": {"@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/slot-2/Ports"},
        "Manufacturer": "MLNX",
        "Model": "BlueField-3 P-Series DPU 200GbE/",
        "Controllers": [
            {
                "Location": {
                    "InfoFormat@Redfish.Deprecated": "The property is deprecated. Please use PartLocation instead.",
                    "Info": "Slot 2",
                    "Info@Redfish.Deprecated": "The property is deprecated. Please use PartLocation instead.",
                    "InfoFormat": "Slot X",
                    "PartLocation": {
                        "ServiceLabel": "PCIe 2",
                        "LocationType": "Slot",
                        "LocationOrdinalValue": 2,
                    },
                },
                "ControllerCapabilities": {
                    "NetworkDeviceFunctionCount": -2,
                    "NetworkPortCount": 2,
                },
                "Links": {
                    "PCIeDevices": [{"@odata.id": "/redfish/v1/Chassis/1/PCIeDevices/slot_2"}],
                    "NetworkDeviceFunctions": [],
                    "Ports": [
                        {"@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/slot-2/Ports/2"},
                        {"@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/slot-2/Ports/2"},
                    ],
                },
                "FirmwarePackageVersion": "32.38.3056",
            }
        ],
        "SKU": "900-9D3B6-00CV-A",
        "@odata.etag": '"a520387e2ea734aa39cc3"',
    }
}


LENOVO_PORT_DETAILS = {
    "2": {
        "Oem": {
            "Lenovo": {
                "@odata.type": "#LenovoPort.v1_0_0.LenovoPort",
                "PhysicalPortMacAddress": "58:a2:e1:84:74:d7",
                "PhysicalPortNumber": "2",
                "PortMaximumMTU": None,
            }
        },
        "FunctionMaxBandwidth": [],
        "CurrentSpeedGbps": None,
        "LinkStatus": "LinkDown",
        "LinkNetworkTechnology": None,
        "Id": "2",
        "@odata.etag": '"4cf2b99bccd328aac38"',
        "@odata.type": "#Port.v1_7_0.Port",
        "@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/slot-2/Ports/2",
        "Status": {"State": "Enabled", "HealthRollup": "OK", "Health": "OK"},
        "Description": "A Network Port represents a discrete physical port capable of connecting to a network.",
        "@odata.context": "/redfish/v1/$metadata#Port.Port",
        "MaxSpeedGbps": None,
        "Name": "Phyical Port 2",
    }
}


DELL_REDFISH_BASE = {
    "@odata.context": "/redfish/v1/$metadata#ServiceRoot.ServiceRoot",
    "@odata.id": "/redfish/v1",
    "@odata.type": "#ServiceRoot.v1_8_0.ServiceRoot",
    "AccountService": {"@odata.id": "/redfish/v1/AccountService"},
    "CertificateService": {"@odata.id": "/redfish/v1/CertificateService"},
    "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
    "Description": "Root Service",
    "EventService": {"@odata.id": "/redfish/v1/EventService"},
    "Fabrics": {"@odata.id": "/redfish/v1/Fabrics"},
    "Id": "RootService",
    "JobService": {"@odata.id": "/redfish/v1/JobService"},
    "JsonSchemas": {"@odata.id": "/redfish/v1/JsonSchemas"},
    "Links": {"Sessions": {"@odata.id": "/redfish/v1/SessionService/Sessions"}},
    "Managers": {"@odata.id": "/redfish/v1/Managers"},
    "Name": "Root Service",
    "Oem": {
        "Dell": {
            "@odata.context": "/redfish/v1/$metadata#DellServiceRoot.DellServiceRoot",
            "@odata.type": "#DellServiceRoot.v1_0_0.DellServiceRoot",
            "IsBranded": 0,
            "ManagerMACAddress": "c8:4b:d6:7a:e9:e2",
            "ServiceTag": "3QWL0R3",
        }
    },
    "Product": "Integrated Dell Remote Access Controller",
    "ProtocolFeaturesSupported": {
        "DeepOperations": {"DeepPATCH": False, "DeepPOST": False},
        "ExcerptQuery": False,
        "ExpandQuery": {
            "ExpandAll": True,
            "Levels": True,
            "Links": True,
            "MaxLevels": 1,
            "NoLinks": True,
        },
        "FilterQuery": True,
        "OnlyMemberQuery": True,
        "SelectQuery": True,
    },
    "RedfishVersion": "1.11.0",
    "Registries": {"@odata.id": "/redfish/v1/Registries"},
    "SessionService": {"@odata.id": "/redfish/v1/SessionService"},
    "Systems": {"@odata.id": "/redfish/v1/Systems"},
    "Tasks": {"@odata.id": "/redfish/v1/TaskService"},
    "TelemetryService": {"@odata.id": "/redfish/v1/TelemetryService"},
    "UpdateService": {"@odata.id": "/redfish/v1/UpdateService"},
    "Vendor": "Dell",
}


DELL_UNAUTHORIZED_RESPONSE = {
    "error": {
        "code": "Base.1.8.GeneralError",
        "message": "A general error has occurred. See ExtendedInfo for more information.",
        "@Message.ExtendedInfo": [
            {
                "@odata.type": "#Message.v1_1_0.Message",
                "MessageId": "Base.1.8.AccessDenied",
                "Message": "The authentication credentials included with this request are missing or invalid.",
                "MessageArgs": [],
                "MessageArgs@odata.count": 0,
                "RelatedProperties": [],
                "RelatedProperties@odata.count": 0,
                "Severity": "Critical",
                "Resolution": "Attempt to ensure that the URI is correct and that the service has the appropriate credentials.",
            }
        ],
    }
}


DELL_NETWORK_ADAPTERS = {
    "@odata.context": "/redfish/v1/$metadata#NetworkAdapterCollection.NetworkAdapterCollection",
    "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters",
    "@odata.type": "#NetworkAdapterCollection.NetworkAdapterCollection",
    "Description": "Collection Of Network Adapter",
    "Members": [
        {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1"},
        {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4"},
        {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5"},
    ],
    "Members@odata.count": 3,
    "Name": "Network Adapter Collection",
}


DELL_SYSTEM_INFO = {
    "@odata.context": "/redfish/v1/$metadata#ComputerSystem.ComputerSystem",
    "@odata.id": "/redfish/v1/Systems/System.Embedded.1",
    "@odata.type": "#ComputerSystem.v1_12_0.ComputerSystem",
    "Actions": {
        "#ComputerSystem.Reset": {
            "target": "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            "ResetType@Redfish.AllowableValues": [
                "On",
                "ForceOff",
                "ForceRestart",
                "GracefulRestart",
                "GracefulShutdown",
                "PushPowerButton",
                "Nmi",
                "PowerCycle",
            ],
        }
    },
    "AssetTag": "",
    "Bios": {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Bios"},
    "BiosVersion": "1.6.5",
    "Boot": {
        "BootOptions": {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/BootOptions"},
        "Certificates": {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Boot/Certificates"},
        "BootOrder": ["Boot0000"],
        "BootOrder@odata.count": 1,
        "BootSourceOverrideEnabled": "Disabled",
        "BootSourceOverrideMode": "UEFI",
        "BootSourceOverrideTarget": "None",
        "UefiTargetBootSourceOverride": None,
        "BootSourceOverrideTarget@Redfish.AllowableValues": [
            "None",
            "Pxe",
            "Floppy",
            "Cd",
            "Hdd",
            "BiosSetup",
            "Utilities",
            "UefiTarget",
            "SDCard",
            "UefiHttp",
        ],
    },
    "Description": "Computer System which represents a machine (physical or virtual) and the local resources such as memory, cpu and other devices that can be accessed from that machine.",
    "EthernetInterfaces": {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/EthernetInterfaces"},
    "HostName": "",
    "HostWatchdogTimer": {
        "FunctionEnabled": False,
        "Status": {"State": "Disabled"},
        "TimeoutAction": "None",
    },
    "HostingRoles": [],
    "HostingRoles@odata.count": 0,
    "Id": "System.Embedded.1",
    "IndicatorLED": "Lit",
    "Links": {
        "Chassis": [{"@odata.id": "/redfish/v1/Chassis/System.Embedded.1"}],
        "Chassis@odata.count": 1,
        "CooledBy": [
            {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Thermal#/Fans/0"},
            {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Thermal#/Fans/1"},
            {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Thermal#/Fans/2"},
            {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Thermal#/Fans/3"},
            {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Thermal#/Fans/4"},
            {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Thermal#/Fans/5"},
        ],
        "CooledBy@odata.count": 6,
        "ManagedBy": [{"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1"}],
        "ManagedBy@odata.count": 1,
        "Oem": {
            "Dell": {
                "@odata.type": "#DellOem.v1_2_0.DellOemLinks",
                "BootOrder": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellBootSources"
                },
                "DellBootSources": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellBootSources"
                },
                "DellSoftwareInstallationService": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellSoftwareInstallationService"
                },
                "DellVideoCollection": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellVideo"
                },
                "DellChassisCollection": {
                    "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Oem/Dell/DellChassis"
                },
                "DellPresenceAndStatusSensorCollection": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellPresenceAndStatusSensors"
                },
                "DellSensorCollection": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellSensors"
                },
                "DellRollupStatusCollection": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRollupStatus"
                },
                "DellPSNumericSensorCollection": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellPSNumericSensors"
                },
                "DellVideoNetworkCollection": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellVideoNetwork"
                },
                "DellOSDeploymentService": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellOSDeploymentService"
                },
                "DellMetricService": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellMetricService"
                },
                "DellGPUSensorCollection": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellGPUSensors"
                },
                "DellRaidService": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
                },
                "DellNumericSensorCollection": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellNumericSensors"
                },
                "DellBIOSService": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellBIOSService"
                },
                "DellSlotCollection": {
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellSlots"
                },
            }
        },
        "PoweredBy": [
            {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Power#/PowerSupplies/0"},
            {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Power#/PowerSupplies/1"},
        ],
        "PoweredBy@odata.count": 2,
    },
    "Manufacturer": "Dell Inc.",
    "Memory": {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Memory"},
    "MemorySummary": {
        "MemoryMirroring": "System",
        "Status": {"Health": "OK", "HealthRollup": "OK", "State": "Enabled"},
        "TotalSystemMemoryGiB": 512,
    },
    "Model": "PowerEdge R750",
    "Name": "System",
    "NetworkInterfaces": {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkInterfaces"},
    "Oem": {
        "Dell": {
            "@odata.type": "#DellOem.v1_2_0.DellOemResources",
            "DellSystem": {
                "BIOSReleaseDate": "04/15/2022",
                "BaseBoardChassisSlot": "NA",
                "BatteryRollupStatus": "OK",
                "BladeGeometry": "NotApplicable",
                "CMCIP": None,
                "CPURollupStatus": "OK",
                "ChassisModel": "",
                "ChassisName": "Main System Chassis",
                "ChassisServiceTag": "3QWL0R3",
                "ChassisSystemHeightUnit": 2,
                "CurrentRollupStatus": "OK",
                "EstimatedExhaustTemperatureCelsius": 33,
                "EstimatedSystemAirflowCFM": 77,
                "ExpressServiceCode": "8157196047",
                "FanRollupStatus": "OK",
                "Id": "System.Embedded.1",
                "IDSDMRollupStatus": None,
                "IntrusionRollupStatus": "OK",
                "IsOEMBranded": "False",
                "LastSystemInventoryTime": "2024-10-09T17:51:35+00:00",
                "LastUpdateTime": "2024-06-06T18:54:35+00:00",
                "LicensingRollupStatus": "OK",
                "ManagedSystemSize": "2 U",
                "MaxCPUSockets": 2,
                "MaxDIMMSlots": 32,
                "MaxPCIeSlots": 8,
                "MemoryOperationMode": "OptimizerMode",
                "Name": "DellSystem",
                "NodeID": "3QWL0R3",
                "PSRollupStatus": "OK",
                "PlatformGUID": "3352304f-c0b3-4c80-5710-00514c4c4544",
                "PopulatedDIMMSlots": 16,
                "PopulatedPCIeSlots": 2,
                "PowerCapEnabledState": "Disabled",
                "SDCardRollupStatus": None,
                "SELRollupStatus": "Error",
                "ServerAllocationWatts": None,
                "StorageRollupStatus": "OK",
                "SysMemErrorMethodology": "Multi-bitECC",
                "SysMemFailOverState": "NotInUse",
                "SysMemLocation": "SystemBoardOrMotherboard",
                "SysMemPrimaryStatus": "OK",
                "SystemGeneration": "15G Monolithic",
                "SystemID": 2318,
                "SystemRevision": "I",
                "TempRollupStatus": "OK",
                "TempStatisticsRollupStatus": "OK",
                "UUID": "4c4c4544-0051-5710-804c-b3c04f305233",
                "VoltRollupStatus": "OK",
                "smbiosGUID": "44454c4c-5100-1057-804c-b3c04f305233",
                "@odata.context": "/redfish/v1/$metadata#DellSystem.DellSystem",
                "@odata.type": "#DellSystem.v1_3_0.DellSystem",
                "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellSystem/System.Embedded.1",
            },
        }
    },
    "PCIeDevices": [
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/0-28"},
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/0-23"},
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/4-0"},
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/177-0"},
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/0-31"},
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/0-17"},
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/3-0"},
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/101-0"},
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/178-0"},
    ],
    "PCIeDevices@odata.count": 9,
    "PCIeFunctions": [
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/0-28/PCIeFunctions/0-28-4"
        },
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/0-23/PCIeFunctions/0-23-0"
        },
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/4-0/PCIeFunctions/4-0-1"},
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/177-0/PCIeFunctions/177-0-1"
        },
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/177-0/PCIeFunctions/177-0-0"
        },
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/0-31/PCIeFunctions/0-31-0"
        },
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/0-28/PCIeFunctions/0-28-0"
        },
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/0-17/PCIeFunctions/0-17-5"
        },
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/3-0/PCIeFunctions/3-0-0"},
        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/4-0/PCIeFunctions/4-0-0"},
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/101-0/PCIeFunctions/101-0-0"
        },
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/0-31/PCIeFunctions/0-31-4"
        },
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/178-0/PCIeFunctions/178-0-1"
        },
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/178-0/PCIeFunctions/178-0-0"
        },
    ],
    "PCIeFunctions@odata.count": 14,
    "PartNumber": "01J4WFA05",
    "PowerState": "On",
    "ProcessorSummary": {
        "Count": 2,
        "LogicalProcessorCount": 96,
        "Model": "Intel(R) Xeon(R) Gold 6336Y CPU @ 2.40GHz",
        "Status": {"Health": "OK", "HealthRollup": "OK", "State": "Enabled"},
    },
    "Processors": {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors"},
    "SKU": "3QWL0R3",
    "SecureBoot": {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/SecureBoot"},
    "SerialNumber": "MXFC40025U00BL",
    "SimpleStorage": {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/SimpleStorage"},
    "Status": {"Health": "Critical", "HealthRollup": "Critical", "State": "Enabled"},
    "Storage": {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Storage"},
    "SystemType": "Physical",
    "TrustedModules": [
        {
            "FirmwareVersion": "7.2.2.0",
            "InterfaceType": "TPM2_0",
            "Status": {"State": "Enabled"},
        }
    ],
    "TrustedModules@odata.count": 1,
    "UUID": "4c4c4544-0051-5710-804c-b3c04f305233",
}


DELL_NETWORK_ADAPTER_DETAILS = {
    "NIC.Embedded.1": {
        "@odata.context": "/redfish/v1/$metadata#NetworkAdapter.NetworkAdapter",
        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1",
        "@odata.type": "#NetworkAdapter.v1_4_0.NetworkAdapter",
        "Assembly": {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Assembly"},
        "Controllers": [
            {
                "ControllerCapabilities": {
                    "DataCenterBridging": {"Capable": False},
                    "NPAR": {"NparCapable": False, "NparEnabled": False},
                    "NPIV": {"MaxDeviceLogins": 0, "MaxPortLogins": 0},
                    "NetworkDeviceFunctionCount": 2,
                    "NetworkPortCount": 2,
                    "VirtualizationOffload": {
                        "SRIOV": {"SRIOVVEPACapable": False},
                        "VirtualFunction": {
                            "DeviceMaxCount": None,
                            "MinAssignmentGroupSize": None,
                            "NetworkPortMaxCount": None,
                        },
                    },
                },
                "FirmwarePackageVersion": "22.0.6",
                "Links": {
                    "NetworkDeviceFunctions": [
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions/NIC.Embedded.2-1-1"
                        },
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions/NIC.Embedded.1-1-1"
                        },
                    ],
                    "NetworkDeviceFunctions@odata.count": 2,
                    "NetworkPorts": [
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkPorts/NIC.Embedded.2-1"
                        },
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkPorts/NIC.Embedded.1-1"
                        },
                    ],
                    "NetworkPorts@odata.count": 2,
                    "Oem": {
                        "Dell": {
                            "@odata.type": "#DellOem.v1_2_0.DellOemLinks",
                            "CPUAffinity": [],
                            "CPUAffinity@odata.count": 0,
                        }
                    },
                },
            }
        ],
        "Controllers@odata.count": 1,
        "Description": "Network Adapter View",
        "Id": "NIC.Embedded.1",
        "Manufacturer": "Broadcom Inc. and subsidiaries",
        "Model": None,
        "Name": "Network Adapter View",
        "NetworkDeviceFunctions": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions"
        },
        "NetworkPorts": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkPorts"
        },
        "PartNumber": None,
        "SerialNumber": None,
        "Status": {"Health": "OK", "HealthRollup": "OK", "State": "Enabled"},
    },
    "NIC.Slot.4": {
        "@odata.context": "/redfish/v1/$metadata#NetworkAdapter.NetworkAdapter",
        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4",
        "@odata.type": "#NetworkAdapter.v1_4_0.NetworkAdapter",
        "Assembly": {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Assembly"},
        "Controllers": [
            {
                "ControllerCapabilities": {
                    "DataCenterBridging": {"Capable": True},
                    "NPAR": {"NparCapable": False, "NparEnabled": False},
                    "NPIV": {"MaxDeviceLogins": 0, "MaxPortLogins": 0},
                    "NetworkDeviceFunctionCount": 2,
                    "NetworkPortCount": 1,
                    "VirtualizationOffload": {
                        "SRIOV": {"SRIOVVEPACapable": False},
                        "VirtualFunction": {
                            "DeviceMaxCount": 254,
                            "MinAssignmentGroupSize": 16,
                            "NetworkPortMaxCount": 127,
                        },
                    },
                },
                "FirmwarePackageVersion": "32.38.3056",
                "Links": {
                    "NetworkDeviceFunctions": [
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkDeviceFunctions/NIC.Slot.4-2"
                        },
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkDeviceFunctions/NIC.Slot.4-1"
                        },
                    ],
                    "NetworkDeviceFunctions@odata.count": 2,
                    "NetworkPorts": [
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkPorts/NIC.Slot.4-2"
                        },
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkPorts/NIC.Slot.4-1"
                        },
                    ],
                    "NetworkPorts@odata.count": 2,
                    "Oem": {
                        "Dell": {
                            "@odata.type": "#DellOem.v1_2_0.DellOemLinks",
                            "CPUAffinity": [
                                {
                                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors/CPU.Socket.2"
                                }
                            ],
                            "CPUAffinity@odata.count": 1,
                        }
                    },
                },
            }
        ],
        "Controllers@odata.count": 1,
        "Description": "Network Adapter View",
        "Id": "NIC.Slot.4",
        "Manufacturer": "Mellanox Technologies",
        "Model": None,
        "Name": "Network Adapter View",
        "NetworkDeviceFunctions": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkDeviceFunctions"
        },
        "NetworkPorts": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkPorts"
        },
        "PartNumber": None,
        "SerialNumber": None,
        "Status": {"Health": None, "HealthRollup": None, "State": "Enabled"},
    },
    "NIC.Slot.5": {
        "@odata.context": "/redfish/v1/$metadata#NetworkAdapter.NetworkAdapter",
        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5",
        "@odata.type": "#NetworkAdapter.v1_4_0.NetworkAdapter",
        "Assembly": {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/Assembly"},
        "Controllers": [
            {
                "ControllerCapabilities": {
                    "DataCenterBridging": {"Capable": True},
                    "NPAR": {"NparCapable": False, "NparEnabled": False},
                    "NPIV": {"MaxDeviceLogins": 0, "MaxPortLogins": 0},
                    "NetworkDeviceFunctionCount": 2,
                    "NetworkPortCount": 1,
                    "VirtualizationOffload": {
                        "SRIOV": {"SRIOVVEPACapable": False},
                        "VirtualFunction": {
                            "DeviceMaxCount": 254,
                            "MinAssignmentGroupSize": 16,
                            "NetworkPortMaxCount": 127,
                        },
                    },
                },
                "FirmwarePackageVersion": "32.38.3056",
                "Links": {
                    "NetworkDeviceFunctions": [
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkDeviceFunctions/NIC.Slot.5-2"
                        },
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkDeviceFunctions/NIC.Slot.5-1"
                        },
                    ],
                    "NetworkDeviceFunctions@odata.count": 2,
                    "NetworkPorts": [
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkPorts/NIC.Slot.5-2"
                        },
                        {
                            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkPorts/NIC.Slot.5-1"
                        },
                    ],
                    "NetworkPorts@odata.count": 2,
                    "Oem": {
                        "Dell": {
                            "@odata.type": "#DellOem.v1_2_0.DellOemLinks",
                            "CPUAffinity": [
                                {
                                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors/CPU.Socket.2"
                                }
                            ],
                            "CPUAffinity@odata.count": 1,
                        }
                    },
                },
            }
        ],
        "Controllers@odata.count": 1,
        "Description": "Network Adapter View",
        "Id": "NIC.Slot.5",
        "Manufacturer": "Mellanox Technologies",
        "Model": None,
        "Name": "Network Adapter View",
        "NetworkDeviceFunctions": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkDeviceFunctions"
        },
        "NetworkPorts": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkPorts"
        },
        "PartNumber": None,
        "SerialNumber": None,
        "Status": {"Health": None, "HealthRollup": None, "State": "Enabled"},
    },
}

DELL_NETWORK_FUNCTION_DETAIL = {
    "NIC.Embedded.1-1-1": {
        "@Redfish.Settings": {
            "@odata.context": "/redfish/v1/$metadata#Settings.Settings",
            "@odata.type": "#Settings.v1_3_1.Settings",
            "SettingsObject": {
                "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions/NIC.Embedded.1-1-1/Settings"
            },
            "SupportedApplyTimes": [
                "OnReset",
                "AtMaintenanceWindowStart",
                "InMaintenanceWindowOnReset",
            ],
        },
        "@odata.context": "/redfish/v1/$metadata#NetworkDeviceFunction.NetworkDeviceFunction",
        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions/NIC.Embedded.1-1-1",
        "@odata.type": "#NetworkDeviceFunction.v1_5_1.NetworkDeviceFunction",
        "AssignablePhysicalPorts": [
            {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkPorts/NIC.Embedded.1-1"
            }
        ],
        "AssignablePhysicalPorts@odata.count": 1,
        "Description": "NetworkDeviceFunction",
        "Ethernet": {
            "MACAddress": "C8:4B:D6:7A:78:B0",
            "MTUSize": None,
            "PermanentMACAddress": "C8:4B:D6:7A:78:B0",
            "VLAN": {"VLANEnable": False, "VLANId": 1},
        },
        "FibreChannel": {
            "BootTargets": [{"LUNID": None, "WWPN": None}],
            "BootTargets@odata.count": 1,
            "FCoEActiveVLANId": None,
            "FCoELocalVLANId": None,
            "PermanentWWNN": None,
            "PermanentWWPN": None,
            "WWNN": None,
            "WWNSource": None,
            "WWPN": None,
        },
        "Id": "NIC.Embedded.1-1-1",
        "Links": {
            "PhysicalPortAssignment": {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkPorts/NIC.Embedded.1-1"
            },
            "Oem": {
                "Dell": {
                    "@odata.type": "#DellOem.v1_2_0.DellOemLinks",
                    "DellNetworkAttributes": {
                        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions/NIC.Embedded.1-1-1/Oem/Dell/DellNetworkAttributes/NIC.Embedded.1-1-1"
                    },
                    "CPUAffinity": [],
                    "CPUAffinity@odata.count": 0,
                }
            },
        },
        "MaxVirtualFunctions": None,
        "Name": "NetworkDeviceFunction",
        "NetDevFuncCapabilities": ["Disabled", "Ethernet"],
        "NetDevFuncCapabilities@odata.count": 2,
        "NetDevFuncType": "Ethernet",
        "Oem": {
            "Dell": {
                "@odata.type": "#DellOem.v1_2_0.DellOemResources",
                "DellNIC": {
                    "BusNumber": 4,
                    "CableLengthMetres": None,
                    "ControllerBIOSVersion": "1.39",
                    "DataBusWidth": "Unknown",
                    "DeviceDescription": "Embedded NIC 1 Port 1 Partition 1",
                    "EFIVersion": "21.6.29",
                    "FCoEOffloadMode": "Unknown",
                    "FQDD": "NIC.Embedded.1-1-1",
                    "FamilyVersion": "22.0.6",
                    "Id": "NIC.Embedded.1-1-1",
                    "IdentifierType": None,
                    "InstanceID": "NIC.Embedded.1-1-1",
                    "LastSystemInventoryTime": "2024-06-06T18:03:25+00:00",
                    "LastUpdateTime": "2024-06-06T18:54:35+00:00",
                    "LinkDuplex": "Unknown",
                    "MediaType": "Base T",
                    "Name": "DellNIC",
                    "NicMode": "Unknown",
                    "PCIDeviceID": "165f",
                    "PCISubDeviceID": "08ff",
                    "PCISubVendorID": "1028",
                    "PCIVendorID": "14e4",
                    "PartNumber": None,
                    "PermanentFCOEMACAddress": None,
                    "PermanentiSCSIMACAddress": None,
                    "ProductName": "Broadcom Gigabit Ethernet BCM5720 - C8:4B:D6:7A:78:B0",
                    "Protocol": "NIC",
                    "Revision": None,
                    "SNAPIState": "Disabled",
                    "SNAPISupport": "NotAvailable",
                    "SerialNumber": None,
                    "SlotLength": "Unknown",
                    "SlotType": "Unknown",
                    "TransceiverPartNumber": None,
                    "TransceiverSerialNumber": None,
                    "TransceiverVendorName": None,
                    "VPISupport": "NotAvailable",
                    "VendorName": "Broadcom Corp",
                    "iScsiOffloadMode": "Unknown",
                    "@odata.context": "/redfish/v1/$metadata#DellNIC.DellNIC",
                    "@odata.type": "#DellNIC.v1_4_0.DellNIC",
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions/NIC.Embedded.1-1-1/Oem/Dell/DellNIC/NIC.Embedded.1-1-1",
                },
                "DellNICPortMetrics": {
                    "DiscardedPkts": 0,
                    "FCCRCErrorCount": None,
                    "FCOELinkFailures": None,
                    "FCOEPktRxCount": None,
                    "FCOEPktTxCount": None,
                    "FCOERxPktDroppedCount": None,
                    "FQDD": "NIC.Embedded.1-1-1",
                    "Id": "NIC.Embedded.1-1-1",
                    "LanFCSRxErrors": None,
                    "LanUnicastPktRXCount": None,
                    "LanUnicastPktTXCount": None,
                    "Name": "DellNICPortMetrics",
                    "OSDriverState": "Operational",
                    "PartitionLinkStatus": None,
                    "PartitionOSDriverState": None,
                    "RDMARxTotalBytes": None,
                    "RDMARxTotalPackets": None,
                    "RDMATotalProtectionErrors": None,
                    "RDMATotalProtocolErrors": None,
                    "RDMATxTotalBytes": None,
                    "RDMATxTotalPackets": None,
                    "RDMATxTotalReadReqPkts": None,
                    "RDMATxTotalSendPkts": None,
                    "RDMATxTotalWritePkts": None,
                    "RXInputPowermW": None,
                    "RXInputPowerStatus": None,
                    "RxBroadcast": 0,
                    "RxBytes": 0,
                    "RxErrorPktAlignmentErrors": 0,
                    "RxErrorPktFCSErrors": 0,
                    "RxFalseCarrierDetection": None,
                    "RxJabberPkt": 0,
                    "RxMutlicastPackets": 0,
                    "RxPauseXOFFFrames": 0,
                    "RxPauseXONFrames": 0,
                    "RxRuntPkt": 0,
                    "RxUnicastPackets": 0,
                    "StartStatisticTime": "2024-10-09T12:53:50-05:00",
                    "StatisticTime": "2024-10-18T12:52:21-05:00",
                    "TXBiasCurrentmA": None,
                    "TXBiasCurrentStatus": None,
                    "TXOutputPowermW": None,
                    "TXOutputPowerStatus": None,
                    "TemperatureCelsius": None,
                    "TemperatureStatus": None,
                    "TxBroadcast": 0,
                    "TxBytes": 0,
                    "TxErrorPktExcessiveCollision": 0,
                    "TxErrorPktLateCollision": 0,
                    "TxErrorPktMultipleCollision": 0,
                    "TxErrorPktSingleCollision": 0,
                    "TxMutlicastPackets": 0,
                    "TxPauseXOFFFrames": 0,
                    "TxPauseXONFrames": 0,
                    "TxUnicastPackets": 0,
                    "VoltageStatus": None,
                    "VoltageValueVolts": None,
                    "@odata.context": "/redfish/v1/$metadata#DellNICPortMetrics.DellNICPortMetrics",
                    "@odata.type": "#DellNICPortMetrics.v1_1_1.DellNICPortMetrics",
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions/NIC.Embedded.1-1-1/Oem/Dell/DellNICPortMetrics/NIC.Embedded.1-1-1",
                },
                "DellNICCapabilities": {
                    "BPESupport": "NotSupported",
                    "CongestionNotification": "NotSupported",
                    "ETS": "NotSupported",
                    "EVBModesSupport": "NotSupported",
                    "FCoEBootSupport": "NotSupported",
                    "FCoEMaxIOsPerSession": 0,
                    "FCoEMaxNPIVPerPort": 0,
                    "FCoEMaxNumberExchanges": 0,
                    "FCoEMaxNumberLogins": 0,
                    "FCoEMaxNumberOfFCTargets": 0,
                    "FCoEMaxNumberOutStandingCommands": 0,
                    "FCoEOffloadSupport": "NotSupported",
                    "FeatureLicensingSupport": "NotSupported",
                    "FlexAddressingSupport": "Supported",
                    "Id": "NIC.Embedded.1-1-1",
                    "IPSecOffloadSupport": "NotSupported",
                    "MACSecSupport": "NotSupported",
                    "Name": "DellNICCapabilities",
                    "NWManagementPassThrough": "Supported",
                    "OSBMCManagementPassThrough": "Supported",
                    "OnChipThermalSensor": "Supported",
                    "OpenFlowSupport": "NotSupported",
                    "PXEBootSupport": "Supported",
                    "PartitionWOLSupport": "NotSupported",
                    "PersistencePolicySupport": "Supported",
                    "PriorityFlowControl": "NotSupported",
                    "RDMASupport": "NotSupported",
                    "RemotePHY": "NotSupported",
                    "TCPChimneySupport": "NotSupported",
                    "VEB": "NotSupported",
                    "VEBVEPAMultiChannel": "NotSupported",
                    "VEBVEPASingleChannel": "NotSupported",
                    "VirtualLinkControl": "NotSupported",
                    "iSCSIBootSupport": "NotSupported",
                    "iSCSIOffloadSupport": "NotSupported",
                    "uEFISupport": "Supported",
                    "@odata.context": "/redfish/v1/$metadata#DellNICCapabilities.DellNICCapabilities",
                    "@odata.type": "#DellNICCapabilities.v1_1_0.DellNICCapabilities",
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions/NIC.Embedded.1-1-1/Oem/Dell/DellNICCapabilities/NIC.Embedded.1-1-1",
                },
                "DellFC": None,
                "DellFCPortMetrics": None,
                "DellFCCapabilities": None,
                "DellInfiniBand": None,
                "DellInfiniBandPortMetrics": None,
                "DellInfiniBandCapabilities": None,
            }
        },
        "PhysicalPortAssignment": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkPorts/NIC.Embedded.1-1"
        },
        "Status": {"State": "Enabled", "Health": "OK", "HealthRollup": "OK"},
        "iSCSIBoot": {},
    },
    "NIC.Embedded.2-1-1": {
        "@Redfish.Settings": {
            "@odata.context": "/redfish/v1/$metadata#Settings.Settings",
            "@odata.type": "#Settings.v1_3_1.Settings",
            "SettingsObject": {
                "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions/NIC.Embedded.2-1-1/Settings"
            },
            "SupportedApplyTimes": [
                "OnReset",
                "AtMaintenanceWindowStart",
                "InMaintenanceWindowOnReset",
            ],
        },
        "@odata.context": "/redfish/v1/$metadata#NetworkDeviceFunction.NetworkDeviceFunction",
        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions/NIC.Embedded.2-1-1",
        "@odata.type": "#NetworkDeviceFunction.v1_5_1.NetworkDeviceFunction",
        "AssignablePhysicalPorts": [
            {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkPorts/NIC.Embedded.2-1"
            }
        ],
        "AssignablePhysicalPorts@odata.count": 1,
        "Description": "NetworkDeviceFunction",
        "Ethernet": {
            "MACAddress": "C8:4B:D6:7A:78:B1",
            "MTUSize": None,
            "PermanentMACAddress": "C8:4B:D6:7A:78:B1",
            "VLAN": {"VLANEnable": False, "VLANId": 1},
        },
        "FibreChannel": {
            "BootTargets": [{"LUNID": None, "WWPN": None}],
            "BootTargets@odata.count": 1,
            "FCoEActiveVLANId": None,
            "FCoELocalVLANId": None,
            "PermanentWWNN": None,
            "PermanentWWPN": None,
            "WWNN": None,
            "WWNSource": None,
            "WWPN": None,
        },
        "Id": "NIC.Embedded.2-1-1",
        "Links": {
            "PhysicalPortAssignment": {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkPorts/NIC.Embedded.2-1"
            },
            "Oem": {
                "Dell": {
                    "@odata.type": "#DellOem.v1_2_0.DellOemLinks",
                    "DellNetworkAttributes": {
                        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkDeviceFunctions/NIC.Embedded.2-1-1/Oem/Dell/DellNetworkAttributes/NIC.Embedded.2-1-1"
                    },
                    "CPUAffinity": [],
                    "CPUAffinity@odata.count": 0,
                }
            },
        },
        "MaxVirtualFunctions": None,
        "Name": "NetworkDeviceFunction",
        "NetDevFuncCapabilities": ["Disabled", "Ethernet"],
        "NetDevFuncCapabilities@odata.count": 2,
        "NetDevFuncType": "Ethernet",
        "Oem": {
            "Dell": {
                "@odata.type": "#DellOem.v1_2_0.DellOemResources",
                "DellNIC": {
                    "BusNumber": 4,
                    "CableLengthMetres": None,
                    "ControllerBIOSVersion": "1.39",
                    "DataBusWidth": "Unknown",
                    "DeviceDescription": "Embedded NIC 1 Port 2 Partition 1",
                    "EFIVersion": "21.6.29",
                    "FCoEOffloadMode": "Unknown",
                    "FQDD": "NIC.Embedded.2-1-1",
                    "FamilyVersion": "22.0.6",
                    "Id": "NIC.Embedded.2-1-1",
                    "IdentifierType": None,
                    "InstanceID": "NIC.Embedded.2-1-1",
                    "LastSystemInventoryTime": "2024-06-06T18:03:25+00:00",
                    "LastUpdateTime": "2022-07-18T01:14:57+00:00",
                    "LinkDuplex": "Unknown",
                    "MediaType": "Base T",
                    "Name": "DellNIC",
                    "NicMode": "Unknown",
                    "PCIDeviceID": "165f",
                    "PCISubDeviceID": "08ff",
                    "PCISubVendorID": "1028",
                    "PCIVendorID": "14e4",
                    "PartNumber": None,
                    "PermanentFCOEMACAddress": None,
                    "PermanentiSCSIMACAddress": None,
                    "ProductName": "Broadcom Gigabit Ethernet BCM5720 - C8:4B:D6:7A:78:B1",
                    "Protocol": "NIC",
                    "Revision": None,
                    "SNAPIState": "Disabled",
                    "SNAPISupport": "NotAvailable",
                    "SerialNumber": None,
                    "SlotLength": "Unknown",
                    "SlotType": "Unknown",
                    "TransceiverPartNumber": None,
                    "TransceiverSerialNumber": None,
                    "TransceiverVendorName": None,
                    "VPISupport": "NotAvailable",
                    "VendorName": "Broadcom Corp",
                    "iScsiOffloadMode": "Unknown",
                    "@odata.context": "/redfish/v1/$metadata#DellNIC.DellNIC",
                    "@odata.type": "#DellNIC.v1_4_0.DellNIC",
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Embedded.2/NetworkDeviceFunctions/NIC.Embedded.2-1-1/Oem/Dell/DellNIC/NIC.Embedded.2-1-1",
                },
                "DellNICPortMetrics": {
                    "DiscardedPkts": 0,
                    "FCCRCErrorCount": None,
                    "FCOELinkFailures": None,
                    "FCOEPktRxCount": None,
                    "FCOEPktTxCount": None,
                    "FCOERxPktDroppedCount": None,
                    "FQDD": "NIC.Embedded.2-1-1",
                    "Id": "NIC.Embedded.2-1-1",
                    "LanFCSRxErrors": None,
                    "LanUnicastPktRXCount": None,
                    "LanUnicastPktTXCount": None,
                    "Name": "DellNICPortMetrics",
                    "OSDriverState": "Non-operational",
                    "PartitionLinkStatus": None,
                    "PartitionOSDriverState": None,
                    "RDMARxTotalBytes": None,
                    "RDMARxTotalPackets": None,
                    "RDMATotalProtectionErrors": None,
                    "RDMATotalProtocolErrors": None,
                    "RDMATxTotalBytes": None,
                    "RDMATxTotalPackets": None,
                    "RDMATxTotalReadReqPkts": None,
                    "RDMATxTotalSendPkts": None,
                    "RDMATxTotalWritePkts": None,
                    "RXInputPowermW": None,
                    "RXInputPowerStatus": None,
                    "RxBroadcast": 0,
                    "RxBytes": 0,
                    "RxErrorPktAlignmentErrors": 0,
                    "RxErrorPktFCSErrors": 0,
                    "RxFalseCarrierDetection": None,
                    "RxJabberPkt": 0,
                    "RxMutlicastPackets": 0,
                    "RxPauseXOFFFrames": 0,
                    "RxPauseXONFrames": 0,
                    "RxRuntPkt": 0,
                    "RxUnicastPackets": 0,
                    "StartStatisticTime": "2024-10-09T12:53:50-05:00",
                    "StatisticTime": "2024-10-18T12:53:01-05:00",
                    "TXBiasCurrentmA": None,
                    "TXBiasCurrentStatus": None,
                    "TXOutputPowermW": None,
                    "TXOutputPowerStatus": None,
                    "TemperatureCelsius": None,
                    "TemperatureStatus": None,
                    "TxBroadcast": 0,
                    "TxBytes": 0,
                    "TxErrorPktExcessiveCollision": 0,
                    "TxErrorPktLateCollision": 0,
                    "TxErrorPktMultipleCollision": 0,
                    "TxErrorPktSingleCollision": 0,
                    "TxMutlicastPackets": 0,
                    "TxPauseXOFFFrames": 0,
                    "TxPauseXONFrames": 0,
                    "TxUnicastPackets": 0,
                    "VoltageStatus": None,
                    "VoltageValueVolts": None,
                    "@odata.context": "/redfish/v1/$metadata#DellNICPortMetrics.DellNICPortMetrics",
                    "@odata.type": "#DellNICPortMetrics.v1_1_1.DellNICPortMetrics",
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Embedded.2/NetworkDeviceFunctions/NIC.Embedded.2-1-1/Oem/Dell/DellNICPortMetrics/NIC.Embedded.2-1-1",
                },
                "DellNICCapabilities": {
                    "BPESupport": "NotSupported",
                    "CongestionNotification": "NotSupported",
                    "ETS": "NotSupported",
                    "EVBModesSupport": "NotSupported",
                    "FCoEBootSupport": "NotSupported",
                    "FCoEMaxIOsPerSession": 0,
                    "FCoEMaxNPIVPerPort": 0,
                    "FCoEMaxNumberExchanges": 0,
                    "FCoEMaxNumberLogins": 0,
                    "FCoEMaxNumberOfFCTargets": 0,
                    "FCoEMaxNumberOutStandingCommands": 0,
                    "FCoEOffloadSupport": "NotSupported",
                    "FeatureLicensingSupport": "NotSupported",
                    "FlexAddressingSupport": "Supported",
                    "Id": "NIC.Embedded.2-1-1",
                    "IPSecOffloadSupport": "NotSupported",
                    "MACSecSupport": "NotSupported",
                    "Name": "DellNICCapabilities",
                    "NWManagementPassThrough": "Supported",
                    "OSBMCManagementPassThrough": "Supported",
                    "OnChipThermalSensor": "Supported",
                    "OpenFlowSupport": "NotSupported",
                    "PXEBootSupport": "Supported",
                    "PartitionWOLSupport": "NotSupported",
                    "PersistencePolicySupport": "Supported",
                    "PriorityFlowControl": "NotSupported",
                    "RDMASupport": "NotSupported",
                    "RemotePHY": "NotSupported",
                    "TCPChimneySupport": "NotSupported",
                    "VEB": "NotSupported",
                    "VEBVEPAMultiChannel": "NotSupported",
                    "VEBVEPASingleChannel": "NotSupported",
                    "VirtualLinkControl": "NotSupported",
                    "iSCSIBootSupport": "NotSupported",
                    "iSCSIOffloadSupport": "NotSupported",
                    "uEFISupport": "Supported",
                    "@odata.context": "/redfish/v1/$metadata#DellNICCapabilities.DellNICCapabilities",
                    "@odata.type": "#DellNICCapabilities.v1_1_0.DellNICCapabilities",
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Embedded.2/NetworkDeviceFunctions/NIC.Embedded.2-1-1/Oem/Dell/DellNICCapabilities/NIC.Embedded.2-1-1",
                },
                "DellFC": None,
                "DellFCPortMetrics": None,
                "DellFCCapabilities": None,
                "DellInfiniBand": None,
                "DellInfiniBandPortMetrics": None,
                "DellInfiniBandCapabilities": None,
            }
        },
        "PhysicalPortAssignment": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Embedded.1/NetworkPorts/NIC.Embedded.2-1"
        },
        "Status": {"State": "Enabled", "Health": "OK", "HealthRollup": "OK"},
        "iSCSIBoot": {},
    },
    "NIC.Slot.4-1": {
        "@Redfish.Settings": {
            "@odata.context": "/redfish/v1/$metadata#Settings.Settings",
            "@odata.type": "#Settings.v1_3_1.Settings",
            "SettingsObject": {
                "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkDeviceFunctions/NIC.Slot.4-1/Settings"
            },
            "SupportedApplyTimes": [
                "OnReset",
                "AtMaintenanceWindowStart",
                "InMaintenanceWindowOnReset",
            ],
        },
        "@odata.context": "/redfish/v1/$metadata#NetworkDeviceFunction.NetworkDeviceFunction",
        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkDeviceFunctions/NIC.Slot.4-1",
        "@odata.type": "#NetworkDeviceFunction.v1_5_1.NetworkDeviceFunction",
        "AssignablePhysicalPorts": [
            {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkPorts/NIC.Slot.4-1"
            }
        ],
        "AssignablePhysicalPorts@odata.count": 1,
        "Description": "NetworkDeviceFunction",
        "Ethernet": {
            "MACAddress": "58:A2:E1:72:DD:A0",
            "MTUSize": None,
            "PermanentMACAddress": "58:A2:E1:72:DD:A0",
            "VLAN": {"VLANEnable": False, "VLANId": 1},
        },
        "FibreChannel": {
            "BootTargets": [{"LUNID": None, "WWPN": None}],
            "BootTargets@odata.count": 1,
            "FCoEActiveVLANId": None,
            "FCoELocalVLANId": None,
            "PermanentWWNN": None,
            "PermanentWWPN": None,
            "WWNN": None,
            "WWNSource": None,
            "WWPN": None,
        },
        "Id": "NIC.Slot.4-1",
        "Links": {
            "PhysicalPortAssignment": {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkPorts/NIC.Slot.4-1"
            },
            "Oem": {
                "Dell": {
                    "@odata.type": "#DellOem.v1_2_0.DellOemLinks",
                    "DellNetworkAttributes": {
                        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkDeviceFunctions/NIC.Slot.4-1/Oem/Dell/DellNetworkAttributes/NIC.Slot.4-1"
                    },
                    "CPUAffinity": [
                        {
                            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors/CPU.Socket.2"
                        }
                    ],
                    "CPUAffinity@odata.count": 1,
                }
            },
        },
        "MaxVirtualFunctions": 127,
        "Name": "NetworkDeviceFunction",
        "NetDevFuncCapabilities": ["Disabled"],
        "NetDevFuncCapabilities@odata.count": 1,
        "NetDevFuncType": "Disabled",
        "Oem": {
            "Dell": {
                "@odata.type": "#DellOem.v1_2_0.DellOemResources",
                "DellNIC": {
                    "BusNumber": 178,
                    "CableLengthMetres": None,
                    "ControllerBIOSVersion": None,
                    "DataBusWidth": "8XOrX8",
                    "DeviceDescription": "NIC in Slot 4 Port 1",
                    "EFIVersion": "14.31.22",
                    "FCoEOffloadMode": "Unknown",
                    "FQDD": "NIC.Slot.4-1",
                    "FamilyVersion": "32.38.3056",
                    "Id": "NIC.Slot.4-1",
                    "IdentifierType": None,
                    "InstanceID": "NIC.Slot.4-1",
                    "LastSystemInventoryTime": "2024-06-06T18:03:25+00:00",
                    "LastUpdateTime": "2024-06-06T18:54:35+00:00",
                    "LinkDuplex": "Unknown",
                    "MediaType": "Base T,KR,KX4",
                    "Name": "DellNIC",
                    "NicMode": "Unknown",
                    "PCIDeviceID": "a2dc",
                    "PCISubDeviceID": "0009",
                    "PCISubVendorID": "15b3",
                    "PCIVendorID": "15b3",
                    "PartNumber": None,
                    "PermanentFCOEMACAddress": None,
                    "PermanentiSCSIMACAddress": None,
                    "ProductName": "Nvidia Network Adapter - 58:A2:E1:72:DD:A0",
                    "Protocol": "Unknown",
                    "Revision": None,
                    "SNAPIState": "Disabled",
                    "SNAPISupport": "NotAvailable",
                    "SerialNumber": None,
                    "SlotLength": "LongLength",
                    "SlotType": "PCIExpressGen4",
                    "TransceiverPartNumber": None,
                    "TransceiverSerialNumber": None,
                    "TransceiverVendorName": None,
                    "VPISupport": "NotAvailable",
                    "VendorName": None,
                    "iScsiOffloadMode": "Unknown",
                    "@odata.context": "/redfish/v1/$metadata#DellNIC.DellNIC",
                    "@odata.type": "#DellNIC.v1_4_0.DellNIC",
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkDeviceFunctions/NIC.Slot.4-1/Oem/Dell/DellNIC/NIC.Slot.4-1",
                },
                "DellNICPortMetrics": None,
                "DellNICCapabilities": None,
                "DellFC": None,
                "DellFCPortMetrics": None,
                "DellFCCapabilities": None,
                "DellInfiniBand": None,
                "DellInfiniBandPortMetrics": None,
                "DellInfiniBandCapabilities": None,
            }
        },
        "PhysicalPortAssignment": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkPorts/NIC.Slot.4-1"
        },
        "Status": {"State": "Enabled", "Health": None, "HealthRollup": None},
        "iSCSIBoot": {
            "AuthenticationMethod": "None",
            "CHAPSecret": None,
            "CHAPUsername": None,
            "IPAddressType": "IPv4",
            "IPMaskDNSViaDHCP": True,
            "InitiatorDefaultGateway": "0.0.0.0",
            "InitiatorIPAddress": "0.0.0.0",
            "InitiatorName": None,
            "InitiatorNetmask": "0.0.0.0",
            "PrimaryDNS": "0.0.0.0",
            "PrimaryLUN": 0,
            "PrimaryTargetIPAddress": "0.0.0.0",
            "PrimaryTargetName": None,
            "PrimaryTargetTCPPort": 3260,
            "PrimaryVLANEnable": None,
            "PrimaryVLANId": None,
            "SecondaryDNS": None,
            "SecondaryLUN": None,
            "SecondaryTargetIPAddress": None,
            "SecondaryTargetName": None,
            "SecondaryTargetTCPPort": None,
            "SecondaryVLANEnable": None,
            "SecondaryVLANId": None,
            "TargetInfoViaDHCP": True,
        },
    },
    "NIC.Slot.4-2": {
        "@Redfish.Settings": {
            "@odata.context": "/redfish/v1/$metadata#Settings.Settings",
            "@odata.type": "#Settings.v1_3_1.Settings",
            "SettingsObject": {
                "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkDeviceFunctions/NIC.Slot.4-2/Settings"
            },
            "SupportedApplyTimes": [
                "OnReset",
                "AtMaintenanceWindowStart",
                "InMaintenanceWindowOnReset",
            ],
        },
        "@odata.context": "/redfish/v1/$metadata#NetworkDeviceFunction.NetworkDeviceFunction",
        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkDeviceFunctions/NIC.Slot.4-2",
        "@odata.type": "#NetworkDeviceFunction.v1_5_1.NetworkDeviceFunction",
        "AssignablePhysicalPorts": [
            {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkPorts/NIC.Slot.4-2"
            }
        ],
        "AssignablePhysicalPorts@odata.count": 1,
        "Description": "NetworkDeviceFunction",
        "Ethernet": {
            "MACAddress": "58:A2:E1:72:DD:A1",
            "MTUSize": None,
            "PermanentMACAddress": "58:A2:E1:72:DD:A1",
            "VLAN": {"VLANEnable": False, "VLANId": 1},
        },
        "FibreChannel": {
            "BootTargets": [{"LUNID": None, "WWPN": None}],
            "BootTargets@odata.count": 1,
            "FCoEActiveVLANId": None,
            "FCoELocalVLANId": None,
            "PermanentWWNN": None,
            "PermanentWWPN": None,
            "WWNN": None,
            "WWNSource": None,
            "WWPN": None,
        },
        "Id": "NIC.Slot.4-2",
        "Links": {
            "PhysicalPortAssignment": {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkPorts/NIC.Slot.4-2"
            },
            "Oem": {
                "Dell": {
                    "@odata.type": "#DellOem.v1_2_0.DellOemLinks",
                    "DellNetworkAttributes": {
                        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkDeviceFunctions/NIC.Slot.4-2/Oem/Dell/DellNetworkAttributes/NIC.Slot.4-2"
                    },
                    "CPUAffinity": [
                        {
                            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors/CPU.Socket.2"
                        }
                    ],
                    "CPUAffinity@odata.count": 1,
                }
            },
        },
        "MaxVirtualFunctions": 127,
        "Name": "NetworkDeviceFunction",
        "NetDevFuncCapabilities": ["Disabled"],
        "NetDevFuncCapabilities@odata.count": 1,
        "NetDevFuncType": "Disabled",
        "Oem": {
            "Dell": {
                "@odata.type": "#DellOem.v1_2_0.DellOemResources",
                "DellNIC": {
                    "BusNumber": 178,
                    "CableLengthMetres": None,
                    "ControllerBIOSVersion": None,
                    "DataBusWidth": "8XOrX8",
                    "DeviceDescription": "NIC in Slot 4 Port 2",
                    "EFIVersion": "14.31.22",
                    "FCoEOffloadMode": "Unknown",
                    "FQDD": "NIC.Slot.4-2",
                    "FamilyVersion": "32.38.3056",
                    "Id": "NIC.Slot.4-2",
                    "IdentifierType": None,
                    "InstanceID": "NIC.Slot.4-2",
                    "LastSystemInventoryTime": "2024-06-06T18:03:25+00:00",
                    "LastUpdateTime": "2024-06-06T18:54:35+00:00",
                    "LinkDuplex": "Unknown",
                    "MediaType": "Base T,KR,KX4",
                    "Name": "DellNIC",
                    "NicMode": "Unknown",
                    "PCIDeviceID": "a2dc",
                    "PCISubDeviceID": "0009",
                    "PCISubVendorID": "15b3",
                    "PCIVendorID": "15b3",
                    "PartNumber": None,
                    "PermanentFCOEMACAddress": None,
                    "PermanentiSCSIMACAddress": None,
                    "ProductName": "Nvidia Network Adapter - 58:A2:E1:72:DD:A1",
                    "Protocol": "Unknown",
                    "Revision": None,
                    "SNAPIState": "Disabled",
                    "SNAPISupport": "NotAvailable",
                    "SerialNumber": None,
                    "SlotLength": "LongLength",
                    "SlotType": "PCIExpressGen4",
                    "TransceiverPartNumber": None,
                    "TransceiverSerialNumber": None,
                    "TransceiverVendorName": None,
                    "VPISupport": "NotAvailable",
                    "VendorName": None,
                    "iScsiOffloadMode": "Unknown",
                    "@odata.context": "/redfish/v1/$metadata#DellNIC.DellNIC",
                    "@odata.type": "#DellNIC.v1_4_0.DellNIC",
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkDeviceFunctions/NIC.Slot.4-2/Oem/Dell/DellNIC/NIC.Slot.4-2",
                },
                "DellNICPortMetrics": None,
                "DellNICCapabilities": None,
                "DellFC": None,
                "DellFCPortMetrics": None,
                "DellFCCapabilities": None,
                "DellInfiniBand": None,
                "DellInfiniBandPortMetrics": None,
                "DellInfiniBandCapabilities": None,
            }
        },
        "PhysicalPortAssignment": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.4/NetworkPorts/NIC.Slot.4-2"
        },
        "Status": {"State": "Enabled", "Health": None, "HealthRollup": None},
        "iSCSIBoot": {
            "AuthenticationMethod": "None",
            "CHAPSecret": None,
            "CHAPUsername": None,
            "IPAddressType": "IPv4",
            "IPMaskDNSViaDHCP": True,
            "InitiatorDefaultGateway": "0.0.0.0",
            "InitiatorIPAddress": "0.0.0.0",
            "InitiatorName": None,
            "InitiatorNetmask": "0.0.0.0",
            "PrimaryDNS": "0.0.0.0",
            "PrimaryLUN": 0,
            "PrimaryTargetIPAddress": "0.0.0.0",
            "PrimaryTargetName": None,
            "PrimaryTargetTCPPort": 3260,
            "PrimaryVLANEnable": None,
            "PrimaryVLANId": None,
            "SecondaryDNS": None,
            "SecondaryLUN": None,
            "SecondaryTargetIPAddress": None,
            "SecondaryTargetName": None,
            "SecondaryTargetTCPPort": None,
            "SecondaryVLANEnable": None,
            "SecondaryVLANId": None,
            "TargetInfoViaDHCP": True,
        },
    },
    "NIC.Slot.5-1": {
        "@Redfish.Settings": {
            "@odata.context": "/redfish/v1/$metadata#Settings.Settings",
            "@odata.type": "#Settings.v1_3_1.Settings",
            "SettingsObject": {
                "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkDeviceFunctions/NIC.Slot.5-1/Settings"
            },
            "SupportedApplyTimes": [
                "OnReset",
                "AtMaintenanceWindowStart",
                "InMaintenanceWindowOnReset",
            ],
        },
        "@odata.context": "/redfish/v1/$metadata#NetworkDeviceFunction.NetworkDeviceFunction",
        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkDeviceFunctions/NIC.Slot.5-1",
        "@odata.type": "#NetworkDeviceFunction.v1_5_1.NetworkDeviceFunction",
        "AssignablePhysicalPorts": [
            {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkPorts/NIC.Slot.5-1"
            }
        ],
        "AssignablePhysicalPorts@odata.count": 1,
        "Description": "NetworkDeviceFunction",
        "Ethernet": {
            "MACAddress": "58:A2:E1:72:B8:F6",
            "MTUSize": None,
            "PermanentMACAddress": "58:A2:E1:72:B8:F6",
            "VLAN": {"VLANEnable": False, "VLANId": 1},
        },
        "FibreChannel": {
            "BootTargets": [{"LUNID": None, "WWPN": None}],
            "BootTargets@odata.count": 1,
            "FCoEActiveVLANId": None,
            "FCoELocalVLANId": None,
            "PermanentWWNN": None,
            "PermanentWWPN": None,
            "WWNN": None,
            "WWNSource": None,
            "WWPN": None,
        },
        "Id": "NIC.Slot.5-1",
        "Links": {
            "PhysicalPortAssignment": {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkPorts/NIC.Slot.5-1"
            },
            "Oem": {
                "Dell": {
                    "@odata.type": "#DellOem.v1_2_0.DellOemLinks",
                    "DellNetworkAttributes": {
                        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkDeviceFunctions/NIC.Slot.5-1/Oem/Dell/DellNetworkAttributes/NIC.Slot.5-1"
                    },
                    "CPUAffinity": [
                        {
                            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors/CPU.Socket.2"
                        }
                    ],
                    "CPUAffinity@odata.count": 1,
                }
            },
        },
        "MaxVirtualFunctions": 127,
        "Name": "NetworkDeviceFunction",
        "NetDevFuncCapabilities": ["Disabled"],
        "NetDevFuncCapabilities@odata.count": 1,
        "NetDevFuncType": "Disabled",
        "Oem": {
            "Dell": {
                "@odata.type": "#DellOem.v1_2_0.DellOemResources",
                "DellNIC": {
                    "BusNumber": 177,
                    "CableLengthMetres": None,
                    "ControllerBIOSVersion": None,
                    "DataBusWidth": "8XOrX8",
                    "DeviceDescription": "NIC in Slot 5 Port 1",
                    "EFIVersion": "14.31.22",
                    "FCoEOffloadMode": "Unknown",
                    "FQDD": "NIC.Slot.5-1",
                    "FamilyVersion": "32.38.3056",
                    "Id": "NIC.Slot.5-1",
                    "IdentifierType": None,
                    "InstanceID": "NIC.Slot.5-1",
                    "LastSystemInventoryTime": "2024-06-06T18:03:25+00:00",
                    "LastUpdateTime": "2024-06-06T18:54:35+00:00",
                    "LinkDuplex": "Unknown",
                    "MediaType": "Base T,KR,KX4",
                    "Name": "DellNIC",
                    "NicMode": "Unknown",
                    "PCIDeviceID": "a2dc",
                    "PCISubDeviceID": "0009",
                    "PCISubVendorID": "15b3",
                    "PCIVendorID": "15b3",
                    "PartNumber": None,
                    "PermanentFCOEMACAddress": None,
                    "PermanentiSCSIMACAddress": None,
                    "ProductName": "Nvidia Network Adapter - 58:A2:E1:72:B8:F6",
                    "Protocol": "Unknown",
                    "Revision": None,
                    "SNAPIState": "Disabled",
                    "SNAPISupport": "NotAvailable",
                    "SerialNumber": None,
                    "SlotLength": "LongLength",
                    "SlotType": "PCIExpressGen4",
                    "TransceiverPartNumber": None,
                    "TransceiverSerialNumber": None,
                    "TransceiverVendorName": None,
                    "VPISupport": "NotAvailable",
                    "VendorName": None,
                    "iScsiOffloadMode": "Unknown",
                    "@odata.context": "/redfish/v1/$metadata#DellNIC.DellNIC",
                    "@odata.type": "#DellNIC.v1_4_0.DellNIC",
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkDeviceFunctions/NIC.Slot.5-1/Oem/Dell/DellNIC/NIC.Slot.5-1",
                },
                "DellNICPortMetrics": None,
                "DellNICCapabilities": None,
                "DellFC": None,
                "DellFCPortMetrics": None,
                "DellFCCapabilities": None,
                "DellInfiniBand": None,
                "DellInfiniBandPortMetrics": None,
                "DellInfiniBandCapabilities": None,
            }
        },
        "PhysicalPortAssignment": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkPorts/NIC.Slot.5-1"
        },
        "Status": {"State": "Enabled", "Health": None, "HealthRollup": None},
        "iSCSIBoot": {
            "AuthenticationMethod": "None",
            "CHAPSecret": None,
            "CHAPUsername": None,
            "IPAddressType": "IPv4",
            "IPMaskDNSViaDHCP": True,
            "InitiatorDefaultGateway": "0.0.0.0",
            "InitiatorIPAddress": "0.0.0.0",
            "InitiatorName": None,
            "InitiatorNetmask": "0.0.0.0",
            "PrimaryDNS": "0.0.0.0",
            "PrimaryLUN": 0,
            "PrimaryTargetIPAddress": "0.0.0.0",
            "PrimaryTargetName": None,
            "PrimaryTargetTCPPort": 3260,
            "PrimaryVLANEnable": None,
            "PrimaryVLANId": None,
            "SecondaryDNS": None,
            "SecondaryLUN": None,
            "SecondaryTargetIPAddress": None,
            "SecondaryTargetName": None,
            "SecondaryTargetTCPPort": None,
            "SecondaryVLANEnable": None,
            "SecondaryVLANId": None,
            "TargetInfoViaDHCP": True,
        },
    },
    "NIC.Slot.5-2": {
        "@Redfish.Settings": {
            "@odata.context": "/redfish/v1/$metadata#Settings.Settings",
            "@odata.type": "#Settings.v1_3_1.Settings",
            "SettingsObject": {
                "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkDeviceFunctions/NIC.Slot.5-2/Settings"
            },
            "SupportedApplyTimes": [
                "OnReset",
                "AtMaintenanceWindowStart",
                "InMaintenanceWindowOnReset",
            ],
        },
        "@odata.context": "/redfish/v1/$metadata#NetworkDeviceFunction.NetworkDeviceFunction",
        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkDeviceFunctions/NIC.Slot.5-2",
        "@odata.type": "#NetworkDeviceFunction.v1_5_1.NetworkDeviceFunction",
        "AssignablePhysicalPorts": [
            {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkPorts/NIC.Slot.5-2"
            }
        ],
        "AssignablePhysicalPorts@odata.count": 1,
        "Description": "NetworkDeviceFunction",
        "Ethernet": {
            "MACAddress": "58:A2:E1:72:B8:F7",
            "MTUSize": None,
            "PermanentMACAddress": "58:A2:E1:72:B8:F7",
            "VLAN": {"VLANEnable": False, "VLANId": 1},
        },
        "FibreChannel": {
            "BootTargets": [{"LUNID": None, "WWPN": None}],
            "BootTargets@odata.count": 1,
            "FCoEActiveVLANId": None,
            "FCoELocalVLANId": None,
            "PermanentWWNN": None,
            "PermanentWWPN": None,
            "WWNN": None,
            "WWNSource": None,
            "WWPN": None,
        },
        "Id": "NIC.Slot.5-2",
        "Links": {
            "PhysicalPortAssignment": {
                "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkPorts/NIC.Slot.5-2"
            },
            "Oem": {
                "Dell": {
                    "@odata.type": "#DellOem.v1_2_0.DellOemLinks",
                    "DellNetworkAttributes": {
                        "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkDeviceFunctions/NIC.Slot.5-2/Oem/Dell/DellNetworkAttributes/NIC.Slot.5-2"
                    },
                    "CPUAffinity": [
                        {
                            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors/CPU.Socket.2"
                        }
                    ],
                    "CPUAffinity@odata.count": 1,
                }
            },
        },
        "MaxVirtualFunctions": 127,
        "Name": "NetworkDeviceFunction",
        "NetDevFuncCapabilities": ["Disabled"],
        "NetDevFuncCapabilities@odata.count": 1,
        "NetDevFuncType": "Disabled",
        "Oem": {
            "Dell": {
                "@odata.type": "#DellOem.v1_2_0.DellOemResources",
                "DellNIC": {
                    "BusNumber": 177,
                    "CableLengthMetres": None,
                    "ControllerBIOSVersion": None,
                    "DataBusWidth": "8XOrX8",
                    "DeviceDescription": "NIC in Slot 5 Port 2",
                    "EFIVersion": "14.31.22",
                    "FCoEOffloadMode": "Unknown",
                    "FQDD": "NIC.Slot.5-2",
                    "FamilyVersion": "32.38.3056",
                    "Id": "NIC.Slot.5-2",
                    "IdentifierType": None,
                    "InstanceID": "NIC.Slot.5-2",
                    "LastSystemInventoryTime": "2024-06-06T18:03:25+00:00",
                    "LastUpdateTime": "2024-06-06T18:54:35+00:00",
                    "LinkDuplex": "Unknown",
                    "MediaType": "Base T,KR,KX4",
                    "Name": "DellNIC",
                    "NicMode": "Unknown",
                    "PCIDeviceID": "a2dc",
                    "PCISubDeviceID": "0009",
                    "PCISubVendorID": "15b3",
                    "PCIVendorID": "15b3",
                    "PartNumber": None,
                    "PermanentFCOEMACAddress": None,
                    "PermanentiSCSIMACAddress": None,
                    "ProductName": "Nvidia Network Adapter - 58:A2:E1:72:B8:F7",
                    "Protocol": "Unknown",
                    "Revision": None,
                    "SNAPIState": "Disabled",
                    "SNAPISupport": "NotAvailable",
                    "SerialNumber": None,
                    "SlotLength": "LongLength",
                    "SlotType": "PCIExpressGen4",
                    "TransceiverPartNumber": None,
                    "TransceiverSerialNumber": None,
                    "TransceiverVendorName": None,
                    "VPISupport": "NotAvailable",
                    "VendorName": None,
                    "iScsiOffloadMode": "Unknown",
                    "@odata.context": "/redfish/v1/$metadata#DellNIC.DellNIC",
                    "@odata.type": "#DellNIC.v1_4_0.DellNIC",
                    "@odata.id": "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkDeviceFunctions/NIC.Slot.5-2/Oem/Dell/DellNIC/NIC.Slot.5-2",
                },
                "DellNICPortMetrics": None,
                "DellNICCapabilities": None,
                "DellFC": None,
                "DellFCPortMetrics": None,
                "DellFCCapabilities": None,
                "DellInfiniBand": None,
                "DellInfiniBandPortMetrics": None,
                "DellInfiniBandCapabilities": None,
            }
        },
        "PhysicalPortAssignment": {
            "@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.5/NetworkPorts/NIC.Slot.5-2"
        },
        "Status": {"State": "Enabled", "Health": None, "HealthRollup": None},
        "iSCSIBoot": {
            "AuthenticationMethod": "None",
            "CHAPSecret": None,
            "CHAPUsername": None,
            "IPAddressType": "IPv4",
            "IPMaskDNSViaDHCP": True,
            "InitiatorDefaultGateway": "0.0.0.0",
            "InitiatorIPAddress": "0.0.0.0",
            "InitiatorName": None,
            "InitiatorNetmask": "0.0.0.0",
            "PrimaryDNS": "0.0.0.0",
            "PrimaryLUN": 0,
            "PrimaryTargetIPAddress": "0.0.0.0",
            "PrimaryTargetName": None,
            "PrimaryTargetTCPPort": 3260,
            "PrimaryVLANEnable": None,
            "PrimaryVLANId": None,
            "SecondaryDNS": None,
            "SecondaryLUN": None,
            "SecondaryTargetIPAddress": None,
            "SecondaryTargetName": None,
            "SecondaryTargetTCPPort": None,
            "SecondaryVLANEnable": None,
            "SecondaryVLANId": None,
            "TargetInfoViaDHCP": True,
        },
    },
}


BLUEFIELD_PASSWORD_RESPONSE = {
    "@Message.ExtendedInfo": [
        {
            "@odata.type": "#Message.v1_1_1.Message",
            "Message": "The request completed successfully.",
            "MessageArgs": [],
            "MessageId": "Base.1.15.0.Success",
            "MessageSeverity": "OK",
            "Resolution": "None",
        }
    ]
}


BLUEFIELD_UNAUTHORIZED_RESPONSE = {
    "error": {
        "@Message.ExtendedInfo": [
            {
                "@odata.type": "#Message.v1_1_1.Message",
                "Message": (
                    "While accessing the resource at"
                    " '/redfish/v1/AccountService/Accounts/root', the service received an"
                    " authorization error 'Invalid username or password'."
                ),
                "MessageArgs": [
                    "/redfish/v1/AccountService/Accounts/root",
                    "Invalid username or password",
                ],
                "MessageId": "Base.1.15.0.ResourceAtUriUnauthorized",
                "MessageSeverity": "Critical",
                "Resolution": (
                    "Ensure that the appropriate access is provided for the service in"
                    " order for it to access the URI."
                ),
            }
        ],
        "code": "Base.1.15.0.ResourceAtUriUnauthorized",
        "message": (
            "While accessing the resource at"
            " '/redfish/v1/AccountService/Accounts/root', the service received an"
            " authorization error 'Invalid username or password'."
        ),
    }
}

BLUEFIELD_FACTORY_RESET_RESPONSE = {
    "@Message.ExtendedInfo": [
        {
            "@odata.type": "#Message.v1_1_1.Message",
            "Message": "The request completed successfully.",
            "MessageArgs": [],
            "MessageId": "Base.1.15.0.Success",
            "MessageSeverity": "OK",
            "Resolution": "None",
        }
    ]
}


BLUEFIELD_REDFISH_BASE = {
    "@odata.id": "/redfish/v1",
    "@odata.type": "#ServiceRoot.v1_15_0.ServiceRoot",
    "AccountService": {"@odata.id": "/redfish/v1/AccountService"},
    "Cables": {"@odata.id": "/redfish/v1/Cables"},
    "CertificateService": {"@odata.id": "/redfish/v1/CertificateService"},
    "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
    "ComponentIntegrity": {"@odata.id": "/redfish/v1/ComponentIntegrity"},
    "EventService": {"@odata.id": "/redfish/v1/EventService"},
    "Fabrics": {"@odata.id": "/redfish/v1/Fabrics"},
    "Id": "RootService",
    "JsonSchemas": {"@odata.id": "/redfish/v1/JsonSchemas"},
    "Links": {
        "ManagerProvidingService": {"@odata.id": "/redfish/v1/Managers/Bluefield_BMC"},
        "Sessions": {"@odata.id": "/redfish/v1/SessionService/Sessions"},
    },
    "Managers": {"@odata.id": "/redfish/v1/Managers"},
    "Name": "Root Service",
    "Product": "Nvidia-BMCMezz",
    "ProtocolFeaturesSupported": {
        "DeepOperations": {"DeepPATCH": False, "DeepPOST": False},
        "ExcerptQuery": False,
        "ExpandQuery": {
            "ExpandAll": True,
            "Levels": True,
            "Links": True,
            "MaxLevels": 6,
            "NoLinks": True,
        },
        "FilterQuery": False,
        "OnlyMemberQuery": True,
        "SelectQuery": True,
    },
    "RedfishVersion": "1.9.0",
    "Registries": {"@odata.id": "/redfish/v1/Registries"},
    "ServiceConditions": {"@odata.id": "/redfish/v1/ServiceConditions"},
    "SessionService": {"@odata.id": "/redfish/v1/SessionService"},
    "Systems": {"@odata.id": "/redfish/v1/Systems"},
    "Tasks": {"@odata.id": "/redfish/v1/TaskService"},
    "TelemetryService": {"@odata.id": "/redfish/v1/TelemetryService"},
    "UUID": "96a02e8a-1743-41e4-b994-924f58fa0864",
    "UpdateService": {"@odata.id": "/redfish/v1/UpdateService"},
    "Vendor": "Nvidia",
}


BLUEFIELD_SYSTEM_INFO = {
    "@Redfish.Settings": {
        "@odata.type": "#Settings.v1_3_5.Settings",
        "SettingsObject": {"@odata.id": "/redfish/v1/Systems/Bluefield/Settings"},
    },
    "@odata.id": "/redfish/v1/Systems/Bluefield",
    "@odata.type": "#ComputerSystem.v1_17_0.ComputerSystem",
    "Actions": {
        "#ComputerSystem.Reset": {
            "@Redfish.ActionInfo": "/redfish/v1/Systems/Bluefield/ResetActionInfo",
            "target": "/redfish/v1/Systems/Bluefield/Actions/ComputerSystem.Reset",
        }
    },
    "Bios": {"@odata.id": "/redfish/v1/Systems/Bluefield/Bios"},
    "Boot": {
        "AutomaticRetryAttempts": 3,
        "AutomaticRetryConfig": "Disabled",
        "AutomaticRetryConfig@Redfish.AllowableValues": ["Disabled", "RetryAttempts"],
        "BootOptions": {"@odata.id": "/redfish/v1/Systems/Bluefield/BootOptions"},
        "BootOrder": [],
        "BootOrderPropertySelection": "BootOrder",
        "BootSourceOverrideEnabled": "Disabled",
        "BootSourceOverrideEnabled@Redfish.AllowableValues": [
            "Once",
            "Continuous",
            "Disabled",
        ],
        "BootSourceOverrideMode": "UEFI",
        "BootSourceOverrideMode@Redfish.AllowableValues": ["Legacy", "UEFI"],
        "BootSourceOverrideTarget": "None",
        "BootSourceOverrideTarget@Redfish.AllowableValues": [
            "None",
            "Pxe",
            "Hdd",
            "Cd",
            "Diags",
            "BiosSetup",
            "Usb",
        ],
        "TrustedModuleRequiredToBoot": "Disabled",
    },
    "BootProgress": {
        "LastState": "None",
        "LastStateTime": "1970-01-01T00:00:00.000000+00:00",
    },
    "Description": "Computer System",
    "GraphicalConsole": {
        "ConnectTypesSupported": ["KVMIP"],
        "MaxConcurrentSessions": 4,
        "ServiceEnabled": True,
    },
    "HostWatchdogTimer": {
        "FunctionEnabled": False,
        "Status": {"State": "Enabled"},
        "TimeoutAction": "ResetSystem",
    },
    "Id": "Bluefield",
    "LastResetTime": "1970-01-01T00:00:00+00:00",
    "Links": {
        "Chassis": [{"@odata.id": "/redfish/v1/Chassis/Bluefield_BMC"}],
        "ManagedBy": [{"@odata.id": "/redfish/v1/Managers/Bluefield_BMC"}],
    },
    "LogServices": {"@odata.id": "/redfish/v1/Systems/Bluefield/LogServices"},
    "Memory": {"@odata.id": "/redfish/v1/Systems/Bluefield/Memory"},
    "MemorySummary": {
        "Status": {"Health": "OK", "HealthRollup": "OK", "State": "Disabled"},
        "TotalSystemMemoryGiB": 0,
    },
    "Name": "Bluefield",
    "PowerRestorePolicy": "AlwaysOn",
    "PowerState": "On",
    "ProcessorSummary": {
        "Count": 0,
        "Status": {"Health": "OK", "HealthRollup": "OK", "State": "Disabled"},
    },
    "Processors": {"@odata.id": "/redfish/v1/Systems/Bluefield/Processors"},
    "SKU": "",
    "SecureBoot": {"@odata.id": "/redfish/v1/Systems/Bluefield/SecureBoot"},
    "SerialConsole": {
        "IPMI": {"ServiceEnabled": True},
        "MaxConcurrentSessions": 15,
        "SSH": {
            "HotKeySequenceDisplay": "Press ~. to exit console",
            "Port": 2200,
            "ServiceEnabled": True,
        },
    },
    "SerialNumber": "MT2402XZ0EC9            ",
    "Status": {"Health": "OK", "HealthRollup": "OK", "State": "Enabled"},
    "Storage": {"@odata.id": "/redfish/v1/Systems/Bluefield/Storage"},
    "SystemType": "Physical",
    "UUID": "",
}


BLUEFIELD_SYS_INFO = {
    "@odata.id": "/redfish/v1/UpdateService/FirmwareInventory/DPU_SYS_IMAGE",
    "@odata.type": "#SoftwareInventory.v1_4_0.SoftwareInventory",
    "Description": "Host image",
    "Id": "DPU_SYS_IMAGE",
    "Members@odata.count": 1,
    "Name": "Software Inventory",
    "RelatedItem": [{"@odata.id": "/redfish/v1/Systems/Bluefield/Bios"}],
    "SoftwareId": "",
    "Status": {
        "Conditions": [],
        "Health": "OK",
        "HealthRollup": "OK",
        "State": "Enabled",
    },
    "Updateable": False,
    "Version": "58a2:e103:0072:dda0",
}


BLUEFIELD_NETWORK_FUNCTIONS = {
    "@odata.id": "/redfish/v1/Chassis/Bluefield_BMC/NetworkAdapters/NvidiaNetworkAdapter/NetworkDeviceFunctions",
    "@odata.type": "#NetworkDeviceFunctionCollection.NetworkDeviceFunctionCollection",
    "Members": [
        {
            "@odata.id": "/redfish/v1/Chassis/Bluefield_BMC/NetworkAdapters/NvidiaNetworkAdapter/NetworkDeviceFunctions/eth0f0"
        },
        {
            "@odata.id": "/redfish/v1/Chassis/Bluefield_BMC/NetworkAdapters/NvidiaNetworkAdapter/NetworkDeviceFunctions/eth1f0"
        },
    ],
    "Members@odata.count": 2,
    "Name": "Network Device Function Collection",
}


BLUEFIELD_NETWORK_FUNCTION_DETAIL = {
    "eth0f0": {
        "@odata.id": "/redfish/v1/Chassis/Bluefield_BMC/NetworkAdapters/NvidiaNetworkAdapter/NetworkDeviceFunctions/eth0f0",
        "@odata.type": "#NetworkDeviceFunction.v1_9_0.NetworkDeviceFunction",
        "Ethernet": {"MACAddress": "02:09:97:fc:30:07", "MTUSize": 1500},
        "Id": "eth0f0",
        "Links": {
            "OffloadSystem": {"@odata.id": "/redfish/v1/Systems/Bluefield"},
            "PhysicalPortAssignment": {
                "@odata.id": "/redfish/v1/Chassis/Bluefield_BMC/NetworkAdapters/NvidiaNetworkAdapter/Ports/eth0"
            },
        },
        "Name": "NetworkDeviceFunction",
        "NetDevFuncCapabilities": ["Ethernet"],
        "NetDevFuncType": "Ethernet",
    },
    "eth1f0": {
        "@odata.id": "/redfish/v1/Chassis/Bluefield_BMC/NetworkAdapters/NvidiaNetworkAdapter/NetworkDeviceFunctions/eth1f0",
        "@odata.type": "#NetworkDeviceFunction.v1_9_0.NetworkDeviceFunction",
        "Ethernet": {"MACAddress": "02:09:97:fc:30:07", "MTUSize": 1500},
        "Id": "eth1f0",
        "Links": {
            "OffloadSystem": {"@odata.id": "/redfish/v1/Systems/Bluefield"},
            "PhysicalPortAssignment": {
                "@odata.id": "/redfish/v1/Chassis/Bluefield_BMC/NetworkAdapters/NvidiaNetworkAdapter/Ports/eth1"
            },
        },
        "Name": "NetworkDeviceFunction",
        "NetDevFuncCapabilities": ["Ethernet"],
        "NetDevFuncType": "Ethernet",
    },
}


BLUEFIELD_CHASSIS = {
    "@odata.id": "/redfish/v1/Chassis/Card1",
    "@odata.type": "#Chassis.v1_21_0.Chassis",
    "Actions": {
        "#Chassis.Reset": {
            "@Redfish.ActionInfo": "/redfish/v1/Chassis/Card1/ResetActionInfo",
            "target": "/redfish/v1/Chassis/Card1/Actions/Chassis.Reset",
        }
    },
    "Assembly": {"@odata.id": "/redfish/v1/Chassis/Card1/Assembly"},
    "ChassisType": "Card",
    "Controls": {"@odata.id": "/redfish/v1/Chassis/Card1/Controls"},
    "EnvironmentMetrics": {"@odata.id": "/redfish/v1/Chassis/Card1/EnvironmentMetrics"},
    "Id": "Card1",
    "Links": {
        "ComputerSystems": [{"@odata.id": "/redfish/v1/Systems/Bluefield"}],
        "Contains": [
            {"@odata.id": "/redfish/v1/Chassis/Bluefield_ERoT"},
            {"@odata.id": "/redfish/v1/Chassis/Bluefield_BMC"},
        ],
        "ManagedBy": [{"@odata.id": "/redfish/v1/Managers/Bluefield_BMC"}],
    },
    "Manufacturer": "Nvidia",
    "Model": "Bluefield 3 SmartNIC Main Card",
    "Name": "Card1",
    "NetworkAdapters": {"@odata.id": "/redfish/v1/Chassis/Card1/NetworkAdapters"},
    "PCIeDevices": {"@odata.id": "/redfish/v1/Chassis/Card1/PCIeDevices"},
    "PCIeSlots": {"@odata.id": "/redfish/v1/Chassis/Card1/PCIeSlots"},
    "PartNumber": "900-9D3B6-00CV-AA0   ",
    "Power": {"@odata.id": "/redfish/v1/Chassis/Card1/Power"},
    "PowerState": "On",
    "PowerSubsystem": {"@odata.id": "/redfish/v1/Chassis/Card1/PowerSubsystem"},
    "SKU": "",
    "Sensors": {"@odata.id": "/redfish/v1/Chassis/Card1/Sensors"},
    "SerialNumber": "MT2402XZ0EC9            ",
    "Status": {
        "Conditions": [],
        "Health": "OK",
        "HealthRollup": "OK",
        "State": "Enabled",
    },
    "Thermal": {"@odata.id": "/redfish/v1/Chassis/Card1/Thermal"},
    "ThermalSubsystem": {"@odata.id": "/redfish/v1/Chassis/Card1/ThermalSubsystem"},
    "TrustedComponents": {"@odata.id": "/redfish/v1/Chassis/Card1/TrustedComponents"},
    "UUID": "",
}


TEST_ARP_TABLES = [
    DeviceArpTable(
        ip_to_mac={
            "127.0.0.1": ["C8:4B:D6:7A:E9:E2"],
            "127.0.0.2": ["38:7C:76:8D:6F:13"],
            "127.0.0.3": ["D0:8E:79:F8:92:44"],
            "127.0.0.4": ["38:7C:76:8D:6f:13"],
        }
    ),
    DeviceArpTable(
        ip_to_mac={
            "127.0.0.5": ["C8:4B:D6:7A:39:E2"],
            "127.0.0.6": ["C8:4B:D6:7A:28:F2"],
            "127.0.0.7": ["D0:8E:79:F8:12:44"],
            "127.0.0.8": ["38:7C:76:8D:8f:13"],
        }
    ),
    DeviceArpTable(
        ip_to_mac={
            "127.0.0.11": ["C8:4B:26:7B:39:C2"],
        }
    ),
]


TEST_BMC_SWITCHES: dict[str, dict[str, Any]] = {
    "mock_device1": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
        "name": "mock_device1",
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "smn-leaf"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "msn4600-cs2fc"},
        "primary_ip4": {"host": "10.0.0.1"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
    },
    "mock_device2": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050232",
        "name": "mock_device2",
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "smn-leaf"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "msn4600-cs2fc"},
        "primary_ip4": {"host": "10.0.0.2"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
    },
    "mock_device3": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050214",
        "name": "mock_device3",
        "platform": {"name": "Arista EOS"},
        "role": {"name": "tan-leaf"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "dcs-7368x-128-bnd-r"},
        "primary_ip4": {"host": "10.0.0.3"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
    },
}

TEST_SERVERS = [
    {
        "id": "86d41e26-2520-58b5-a3f0-2d5e507e8b22",
        "name": "rno1-m04-c10-server1.lab1",
        "role": {"name": "tenant-a-device"},
        "tenant": {"name": "invalid-tenant"},
        "device_type": {"model": "poweredge-r750"},
        "platform": {"name": "Linux"},
        "location": {"location_type": {"name": "Site"}, "name": "test_site"},
        "primary_ip4": {"host": "10.180.166.60"},
        "primary_ip6": None,
        "device_bays": [
            {
                "id": "9e81c424-fd20-494d-a9cd-68fe713a33c9",
                "name": "1",
                "installed_device": {"id": "3046d89c-5758-404a-879d-004fbdb96dd9"},
            },
            {
                "id": "39a92e0a-a6be-41d0-8ebc-8fe1b40356b2",
                "name": "2",
                "installed_device": {"id": "fff10e3c-05c8-4cb7-b4f4-636fa9060fd8"},
            },
        ],
        "interfaces": [
            {
                "id": "aa9d8e49-16d4-43a1-beba-069ae9e8c55f",
                "name": "bmc",
                "mac_address": "C8:4B:D6:7A:E9:E2",
                "ip_addresses": [{"host": "10.180.166.60"}],
                "device": {"name": "rno1-m04-c10-server1.lab1"},
            }
        ],
    },
    {
        "id": "a5e9d91d-f089-4e76-8e94-5566e3963b03",
        "name": "rno1-m04-c10-server4.lab1",
        "role": {"name": "server"},
        "platform": None,
        "device_type": {"model": "thinksystem-sr655"},
        "primary_ip4": None,
        "primary_ip6": None,
        "location": {"location_type": {"name": "Site"}, "name": "RNO1-NVIDIA Config Manager-LAB"},
        "device_bays": [
            {
                "id": "9e3401cc-71cf-424f-9032-234384870340",
                "name": "1",
                "installed_device": {"id": "3bf3d6a7-df68-4616-97db-372005460fa0"},
            }
        ],
        "interfaces": [
            {
                "id": "eea2ebd5-201a-4738-9294-f4b60ed7cc8d",
                "name": "bmc",
                "mac_address": "38:7C:76:8D:6F:13",
                "ip_addresses": [],
            }
        ],
    },
]


TEST_DPU_DEVICES: Any = [
    {
        "id": "3046d89c-5758-404a-879d-004fbdb96dd9",
        "name": "rno1-m04-c10-server1-dpu1.lab1",
        "role": {"name": "gpu"},
        "platform": {"name": "Linux"},
        "device_type": {"model": "bluefield-3140"},
        "primary_ip4": {"host": "10.180.166.41"},
        "primary_ip6": None,
        "location": {"location_type": {"name": "Site"}, "name": "RNO1-NVIDIA Config Manager-LAB"},
        "device_bays": [],
        "serial": "",
        "interfaces": [
            {
                "id": "1ac00501-7ada-4edb-94fc-ec39fe0fb0ed",
                "name": "DPU BMC",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu1.lab1"},
            },
            {
                "id": "d29c23c5-ee99-4b1b-a3b7-242482817213",
                "name": "DPU Port 1",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu1.lab1"},
            },
            {
                "id": "336a0f83-d05e-46d3-92af-7b806733153f",
                "name": "DPU Port 2",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu1.lab1"},
            },
        ],
    },
    {
        "id": "fff10e3c-05c8-4cb7-b4f4-636fa9060fd8",
        "name": "rno1-m04-c10-server1-dpu2.lab1",
        "role": {"name": "gpu"},
        "platform": {"name": "Linux"},
        "device_type": {"model": "bluefield-3140"},
        "primary_ip4": {"host": "10.180.166.41"},
        "primary_ip6": None,
        "location": {"location_type": {"name": "Site"}, "name": "RNO1-NVIDIA Config Manager-LAB"},
        "device_bays": [],
        "serial": "",
        "interfaces": [
            {
                "id": "7c3a1063-50a1-45c5-aa47-b04afe18e498",
                "name": "DPU BMC",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu2.lab1"},
            },
            {
                "id": "ee1e0539-cec0-473e-b490-a792055a219d",
                "name": "DPU Port 1",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu2.lab1"},
            },
            {
                "id": "88136029-2d9d-49a8-b820-3d6b884d544e",
                "name": "DPU Port 2",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu2.lab1"},
            },
        ],
    },
    {
        "id": "3bf3d6a7-df68-4616-97db-372005460fa0",
        "name": "rno1-m04-c10-server4-dpu1.lab1",
        "role": {"name": "dpu"},
        "device_type": {"model": "bluefield-3140"},
        "location": {"location_type": {"name": "Site"}, "name": "RNO1-NVIDIA Config Manager-LAB"},
        "device_bays": [],
        "serial": "",
        "interfaces": [
            {
                "id": "be8e95da-ce03-47fa-9dcf-2fbbf340f08a",
                "name": "DPU BMC",
                "mac_address": "58:A2:E1:84:74:FB",
                "device": {"name": "rno1-m04-c10-server4-dpu1.lab1"},
            },
            {
                "id": "36364607-21f5-45d2-9908-d08fee457aab",
                "name": "DPU Port 1",
                "mac_address": "58:A2:E1:84:74:D7",
                "device": {"name": "rno1-m04-c10-server4-dpu1.lab1"},
            },
        ],
    },
]


TEST_REDFISH_DPUS = [
    RedfishDpu(
        address="10.180.166.41",
        port=443,
        vendor=RedfishVendor.BLUEFIELD,
        mac="C8-4B-D6-7A-E9-E2",
        base_mac="58-A2-E1-72-DD-A0",
        serial="MT2402XZ0EC9",
        ports=[
            RedfishDpuPort(name="eth0", mac="58-A2-E1-72-DD-B1"),
            RedfishDpuPort(name="eth1", mac="58-A2-E1-72-DD-B2"),
        ],
    ),
    RedfishDpu(
        address="127.0.0.1",
        port=443,
        vendor=RedfishVendor.DELL,
        mac="C9-4B-D6-7A-E9-E2",
        base_mac="58-A2-E1-72-DD-F0",
        serial="MT2402XZ0EC8",
        ports=[
            RedfishDpuPort(name="eth0", mac="58-A2-E1-72-DE-01"),
            RedfishDpuPort(name="eth1", mac="58-A2-E1-72-DE-02"),
        ],
    ),
]
